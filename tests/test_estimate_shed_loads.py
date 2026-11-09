"""Tests for tools/estimate_shed_loads.py: loads with no meter behind the Shed plug, from the plug's own readings.

The Shed plug reports in sparse snapshots (steps of tens of watts plus an hourly refresh), so the loads behind it are
estimated at the instants it updated: Shed reading minus a continuous model of the Quartz server's wall power (from the
1 s GPU and CPU collectors) minus the other metered children, then a least-squares fit of the residual against the
intervals Nick logged. Recorded because on 2026-11-09 the gym lights were switched on in the shed with no plug of their own.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))

import estimate_shed_loads as esl  # noqa: E402


class TestSeriesHelpers:
    def test_update_instants_keep_only_changes(self):
        points = [(0, 100.0), (5, 100.0), (10, 120.0), (15, 120.0), (20, 90.0)]
        assert esl.update_instants(points) == [(10, 120.0), (20, 90.0)]

    def test_update_instants_of_nothing(self):
        assert esl.update_instants([]) == []
        assert esl.update_instants([(0, 1.0)]) == []

    def test_window_mean_covers_the_window_ending_at_t_minus_lag(self):
        points = [(t, float(t)) for t in range(0, 20)]
        assert esl.window_mean(points, 10, width=4, lag=0) == pytest.approx(8.5)  # t = 7..10
        assert esl.window_mean(points, 10, width=4, lag=-2) == pytest.approx(10.5)  # t = 9..12
        assert esl.window_mean(points, 10, width=4, lag=2) == pytest.approx(6.5)  # t = 5..8

    def test_window_mean_without_points_is_none(self):
        assert esl.window_mean([(0, 1.0)], 100, width=5) is None

    def test_hold_returns_latest_value_not_after_t(self):
        points = [(0, 1.0), (10, 2.0), (20, 3.0)]
        assert esl.hold(points, 15) == 2.0
        assert esl.hold(points, 20) == 3.0
        assert esl.hold(points, 50, max_age=40) == 3.0

    def test_hold_gives_none_before_the_first_point_or_when_too_old(self):
        points = [(10, 2.0)]
        assert esl.hold(points, 5) is None
        assert esl.hold(points, 100, max_age=40) is None


class TestLeastSquares:
    def test_recovers_exact_coefficients(self):
        rows = [[1.0, g, c] for g, c in [(0, 0), (100, 10), (50, 40), (150, 5), (20, 30), (80, 80)]]
        y = [87 + 1.2 * r[1] + 5.0 * r[2] for r in rows]
        beta, sd = esl.fit_least_squares(rows, y)
        assert beta == pytest.approx([87.0, 1.2, 5.0], abs=1e-6)
        assert sd == pytest.approx(0.0, abs=1e-6)

    def test_residual_sd_reflects_noise(self):
        rows = [[1.0, float(i)] for i in range(10)]
        y = [10 + 2 * i + (5 if i % 2 else -5) for i in range(10)]
        _, sd = esl.fit_least_squares(rows, y)
        assert 4 < sd < 8

    def test_too_few_rows_is_an_error(self):
        with pytest.raises(ValueError):
            esl.fit_least_squares([[1.0, 2.0]], [1.0])


class TestQuartzModel:
    def test_predicts_wall_power_from_gpu_and_cpu(self):
        model = esl.QuartzModel(beta=(87.0, 1.2, 5.0), sd=25.0)
        assert model.predict(gpu_w=10.0, cpu_load=2.0) == pytest.approx(87 + 12 + 10)

    def test_fit_from_plug_updates_and_collector_series(self):
        gpu = [(t, 10.0 if t < 500 else 170.0) for t in range(0, 1000)]
        cpu = [(t, 2.0 + (t % 50) / 10) for t in range(0, 1000, 5)]
        truth = esl.QuartzModel(beta=(87.0, 1.2, 5.0), sd=0.0)
        updates = []
        for t in range(100, 900, 37):
            g = esl.window_mean(gpu, t, esl.GPU_WINDOW, esl.QUARTZ_LAG)
            c = esl.window_mean(cpu, t, esl.CPU_WINDOW, esl.QUARTZ_LAG)
            updates.append((t, truth.predict(g, c)))
        model = esl.fit_quartz_model(updates, gpu, cpu)
        assert model.beta == pytest.approx((87.0, 1.2, 5.0), abs=1e-3)
        assert model.sd == pytest.approx(0.0, abs=1e-3)


class TestResiduals:
    def test_residual_is_shed_less_quartz_model_less_other_children(self):
        model = esl.QuartzModel(beta=(100.0, 0.0, 0.0), sd=10.0)
        gpu = [(t, 10.0) for t in range(0, 100)]
        cpu = [(t, 3.0) for t in range(0, 100, 5)]
        freezer = [(30, 40.0)]
        shed_updates = [(50, 300.0)]
        rows = esl.residuals(shed_updates, model, gpu, cpu, {'freezer': freezer})
        assert rows == [esl.Residual(t=50, shed=300.0, quartz=100.0, children=40.0, resid=160.0)]

    def test_instants_without_collector_data_are_skipped(self):
        model = esl.QuartzModel(beta=(100.0, 0.0, 0.0), sd=10.0)
        assert esl.residuals([(50, 300.0)], model, [], [], {}) == []

    def test_missing_children_count_as_zero(self):
        model = esl.QuartzModel(beta=(100.0, 0.0, 0.0), sd=10.0)
        gpu = [(t, 10.0) for t in range(0, 100)]
        cpu = [(t, 3.0) for t in range(0, 100, 5)]
        (row,) = esl.residuals([(50, 300.0)], model, gpu, cpu, {'gone': []})
        assert row.children == 0.0 and row.resid == 200.0


class TestEstimateLoads:
    def obs(self, base, shed_lights, gym):
        """Synthetic residuals: quiet before 100, shed lights from 100, plus gym lights from 200 to 300, quiet after 400."""
        rows = []
        for t in (10, 20, 30, 410, 420, 430):
            rows.append((t, base))
        for t in (110, 120, 130, 140):
            rows.append((t, base + shed_lights))
        for t in (210, 220, 230, 240, 250):
            rows.append((t, base + shed_lights + gym))
        return rows

    def test_recovers_loads_and_baseline_without_noise(self):
        obs = self.obs(base=75.0, shed_lights=20.0, gym=80.0)
        fit = esl.estimate_loads(obs, {'shed lights': [(100, 300)], 'gym lights': [(200, 300)]}, sigma=25.0)
        assert fit.baseline.watts == pytest.approx(75.0)
        # intervals overlap: the shed lights are on while the gym lights are on
        assert fit.loads['shed lights'].watts == pytest.approx(20.0)
        assert fit.loads['gym lights'].watts == pytest.approx(80.0)

    def test_standard_error_scales_with_sigma_and_falls_with_more_observations(self):
        obs = self.obs(base=75.0, shed_lights=20.0, gym=80.0)
        intervals = {'shed lights': [(100, 300)], 'gym lights': [(200, 300)]}
        low = esl.estimate_loads(obs, intervals, sigma=10.0).loads['gym lights'].se
        high = esl.estimate_loads(obs, intervals, sigma=20.0).loads['gym lights'].se
        assert high == pytest.approx(2 * low)
        extra = [(t, 175.0) for t in (260, 270, 280, 290)]  # four more readings with both lights on
        more = esl.estimate_loads(obs + extra, intervals, sigma=10.0).loads['gym lights'].se
        assert more < low

    def test_interval_without_an_observation_is_an_error(self):
        obs = self.obs(base=75.0, shed_lights=20.0, gym=80.0)
        with pytest.raises(ValueError, match='gym lights'):
            esl.estimate_loads(obs, {'gym lights': [(1000, 2000)]}, sigma=10.0)

    def test_no_quiet_observation_is_an_error(self):
        with pytest.raises(ValueError, match='baseline'):
            esl.estimate_loads([(10, 100.0), (20, 110.0)], {'x': [(0, 100)]}, sigma=10.0)

    def test_rows_inside_an_excluded_span_are_ignored(self):
        obs = self.obs(base=75.0, shed_lights=20.0, gym=80.0) + [(150, 999.0)]
        fit = esl.estimate_loads(
            obs, {'shed lights': [(100, 300)], 'gym lights': [(200, 300)]}, sigma=25.0, exclude=[(145, 155)]
        )
        assert fit.loads['gym lights'].watts == pytest.approx(80.0)


class TestParsing:
    def test_clock_span_on_the_start_date(self):
        start = esl.parse_clock('2026-11-09T16:00')
        spans = esl.parse_spans('18:20-18:40,19:00-19:10', day_start=start)
        assert spans == [
            (start + 2 * 3600 + 20 * 60, start + 2 * 3600 + 40 * 60),
            (start + 3 * 3600, start + 3 * 3600 + 600),
        ]

    def test_load_option_splits_name_and_spans(self):
        start = esl.parse_clock('2026-11-09T16:00')
        name, spans = esl.parse_load('gym lights=18:20-18:40', day_start=start)
        assert name == 'gym lights' and len(spans) == 1

    def test_span_that_ends_before_it_starts_is_an_error(self):
        with pytest.raises(ValueError):
            esl.parse_spans('18:40-18:20', day_start=esl.parse_clock('2026-11-09T16:00'))
