from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import (
    db,
    InfluxIntegrationConfig,
    IrrigationPredictionModel,
    Location,
    Sensor,
    User,
    SENSOR_TYPE_IRRIGATION,
    SENSOR_TYPE_SOIL_MOISTURE,
)
from app.services import irrigation_prediction_service
from app.services import irrigation_prediction_scheduler


class FakeAdapter:
    def __init__(self, points_by_key):
        self.points_by_key = points_by_key
        self.query_count = 0

    def query_sensor(self, sensor, start, stop=None):
        self.query_count += 1
        return self.points_by_key.get(sensor.key, [])

    def query_latest_sensor_value(self, sensor, start, stop=None):
        points = self.query_sensor(sensor, start, stop)
        return points[-1] if points else None


class FilteringRecordingAdapter(FakeAdapter):
    def __init__(self, points_by_key):
        super().__init__(points_by_key)
        self.queries = []

    def query_sensor(self, sensor, start, stop=None):
        self.query_count += 1
        self.queries.append((sensor.key, start, stop))
        filtered = []
        for point in self.points_by_key.get(sensor.key, []):
            point_time = datetime.fromisoformat(str(point['time']).replace('Z', '+00:00'))
            if start <= point_time <= (stop or point_time):
                filtered.append(point)
        return filtered


class IrrigationPredictionServiceTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.close(self.db_fd)
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
        os.environ['IRRIGATION_PREDICTION_TRAIN_CRON_ENABLED'] = 'false'
        self.app = create_app()
        self.app.config.update(TESTING=True, WIDGET_API_KEY='secret')
        self.client = self.app.test_client()
        with self.app.app_context():
            user = User(sub='ml-user', name='ML User')
            location = Location(name='Beet 1', target_soil_moisture_percent=55)
            trash = Location(name='Papierkorb')
            db.session.add_all([user, location, trash])
            db.session.flush()
            self.location_id = location.id
            self.trash_location_id = trash.id
            db.session.add_all([
                Sensor(name='Feuchte', key='soil', sensor_type=SENSOR_TYPE_SOIL_MOISTURE, creator_id=user.id, is_active=True, locations=[location]),
                Sensor(name='Bewässerung', key='irrigation', sensor_type=SENSOR_TYPE_IRRIGATION, creator_id=user.id, is_active=True, locations=[location]),
            ])
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)
        os.environ.pop('IRRIGATION_PREDICTION_TRAIN_CRON_ENABLED', None)

    def test_clamps_negative_and_too_large_predictions(self):
        self.assertEqual(irrigation_prediction_service.clamp_minutes(-5, 120), 0)
        self.assertEqual(irrigation_prediction_service.clamp_minutes(125, 120), 120)

    def test_trains_model_and_reuses_it_within_week(self):
        now = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
        soil_points = []
        irrigation_points = []
        for days_ago, soil, minutes in [(3, 45, 35), (2, 48, 25), (1, 52, 10)]:
            day = (now - timedelta(days=days_ago)).replace(hour=6, minute=0, second=0, microsecond=0)
            soil_points.append({'time': day.isoformat(), 'value': soil})
            irrigation_points.extend([
                {'time': day.replace(hour=7).isoformat(), 'value': 1},
                {'time': (day.replace(hour=7) + timedelta(minutes=minutes)).isoformat(), 'value': 0},
            ])
        soil_points.append({'time': now.isoformat(), 'value': 45})
        adapter = FakeAdapter({'soil': soil_points, 'irrigation': irrigation_points})

        with self.app.app_context():
            location = db.session.get(Location, self.location_id)
            prediction = irrigation_prediction_service.predict_for_location(
                location,
                55,
                max_minutes=120,
                adapter=adapter,
                now=now,
            )
            self.assertTrue(prediction['trained_now'])
            self.assertEqual(prediction['source'], 'model')
            self.assertGreaterEqual(prediction['predicted_minutes'], 0)
            self.assertLessEqual(prediction['predicted_minutes'], 120)
            query_count_after_training = adapter.query_count

            second_prediction = irrigation_prediction_service.predict_for_location(
                location,
                55,
                max_minutes=120,
                adapter=adapter,
                now=now + timedelta(days=1),
            )
            self.assertFalse(second_prediction['trained_now'])
            self.assertLess(adapter.query_count - query_count_after_training, query_count_after_training)

    def test_train_due_models_trains_due_locations(self):
        now = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
        soil_points = []
        irrigation_points = []
        for days_ago, soil, minutes in [(3, 45, 35), (2, 48, 25), (1, 52, 10)]:
            day = (now - timedelta(days=days_ago)).replace(hour=6, minute=0, second=0, microsecond=0)
            soil_points.append({'time': day.isoformat(), 'value': soil})
            irrigation_points.extend([
                {'time': day.replace(hour=7).isoformat(), 'value': 1},
                {'time': (day.replace(hour=7) + timedelta(minutes=minutes)).isoformat(), 'value': 0},
            ])
        adapter = FakeAdapter({'soil': soil_points, 'irrigation': irrigation_points})

        with self.app.app_context():
            summary = irrigation_prediction_service.train_due_models(now=now, adapter=adapter)

            self.assertEqual(summary['checked'], 1)
            self.assertEqual(summary['trained'], 1)
            self.assertEqual(summary['failed'], 0)
            model = IrrigationPredictionModel.query.filter_by(location_id=self.location_id).one()
            self.assertEqual(model.trained_at.replace(tzinfo=timezone.utc), now)
            self.assertIsNone(IrrigationPredictionModel.query.filter_by(location_id=self.trash_location_id).one_or_none())

    def test_trash_location_gets_no_prediction_or_model(self):
        now = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
        adapter = FakeAdapter({'soil': [{'time': now.isoformat(), 'value': 40}], 'irrigation': []})

        with self.app.app_context():
            trash = db.session.get(Location, self.trash_location_id)
            prediction = irrigation_prediction_service.predict_for_location(
                trash,
                55,
                max_minutes=120,
                adapter=adapter,
                now=now,
            )

            self.assertIsNone(prediction['predicted_minutes'])
            self.assertIsNone(prediction['model'])
            self.assertFalse(prediction['trained_now'])
            self.assertEqual(prediction['source'], 'unavailable')
            self.assertEqual(adapter.query_count, 0)
            self.assertIsNone(IrrigationPredictionModel.query.filter_by(location_id=self.trash_location_id).one_or_none())
            with self.assertRaises(ValueError):
                irrigation_prediction_service.train_model_for_location(trash, 55, adapter, now=now)
            self.assertIsNone(IrrigationPredictionModel.query.filter_by(location_id=self.trash_location_id).one_or_none())

    def test_training_lookback_is_capped_at_900_days_and_starts_at_first_soil_point(self):
        now = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
        too_old = (now - timedelta(days=950)).replace(hour=6, minute=0, second=0, microsecond=0)
        first_soil = (now - timedelta(days=800)).replace(hour=6, minute=0, second=0, microsecond=0)
        second_soil = (now - timedelta(days=799)).replace(hour=6, minute=0, second=0, microsecond=0)
        adapter = FilteringRecordingAdapter({
            'soil': [
                {'time': too_old.isoformat(), 'value': 41},
                {'time': first_soil.isoformat(), 'value': 45},
                {'time': second_soil.isoformat(), 'value': 48},
            ],
            'irrigation': [
                {'time': first_soil.replace(hour=7).isoformat(), 'value': 1},
                {'time': (first_soil.replace(hour=7) + timedelta(minutes=20)).isoformat(), 'value': 0},
                {'time': second_soil.replace(hour=7).isoformat(), 'value': 1},
                {'time': (second_soil.replace(hour=7) + timedelta(minutes=10)).isoformat(), 'value': 0},
            ],
        })

        with self.app.app_context():
            location = db.session.get(Location, self.location_id)
            irrigation_prediction_service.train_model_for_location(
                location,
                55,
                adapter,
                now=now,
                training_lookback=timedelta(days=1200),
            )

            model = IrrigationPredictionModel.query.filter_by(location_id=self.location_id).one()
            self.assertEqual(model.sample_count, 2)

        soil_query = next(query for query in adapter.queries if query[0] == 'soil')
        irrigation_query = next(query for query in adapter.queries if query[0] == 'irrigation')
        self.assertEqual(soil_query[1], now - timedelta(days=900))
        self.assertEqual(irrigation_query[1], first_soil)

    def test_training_uses_no_days_before_first_available_soil_sensor_data(self):
        now = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
        old_day = (now - timedelta(days=20)).replace(hour=6, minute=0, second=0, microsecond=0)
        first_soil = (now - timedelta(days=4)).replace(hour=6, minute=0, second=0, microsecond=0)
        second_soil = (now - timedelta(days=3)).replace(hour=6, minute=0, second=0, microsecond=0)
        adapter = FilteringRecordingAdapter({
            'soil': [
                {'time': first_soil.isoformat(), 'value': 45},
                {'time': second_soil.isoformat(), 'value': 48},
            ],
            'irrigation': [
                {'time': old_day.replace(hour=7).isoformat(), 'value': 1},
                {'time': (old_day.replace(hour=7) + timedelta(minutes=60)).isoformat(), 'value': 0},
                {'time': first_soil.replace(hour=7).isoformat(), 'value': 1},
                {'time': (first_soil.replace(hour=7) + timedelta(minutes=20)).isoformat(), 'value': 0},
                {'time': second_soil.replace(hour=7).isoformat(), 'value': 1},
                {'time': (second_soil.replace(hour=7) + timedelta(minutes=10)).isoformat(), 'value': 0},
            ],
        })

        with self.app.app_context():
            location = db.session.get(Location, self.location_id)
            irrigation_prediction_service.train_model_for_location(
                location,
                55,
                adapter,
                now=now,
                training_lookback=timedelta(days=900),
            )

            model = IrrigationPredictionModel.query.filter_by(location_id=self.location_id).one()
            self.assertEqual(model.sample_count, 2)

        irrigation_query = next(query for query in adapter.queries if query[0] == 'irrigation')
        self.assertEqual(irrigation_query[1], first_soil)

    def test_train_due_models_skips_without_influx_config(self):
        with self.app.app_context():
            summary = irrigation_prediction_service.train_due_models()

        self.assertTrue(summary['skipped'])
        self.assertEqual(summary['reason'], 'InfluxDB ist nicht vollständig konfiguriert.')

    def test_scheduler_uses_default_three_am_and_next_daily_run(self):
        schedule_time = irrigation_prediction_scheduler.parse_daily_time('')
        self.assertEqual((schedule_time.hour, schedule_time.minute), (3, 0))

        before_three = datetime(2026, 6, 5, 2, 30, tzinfo=timezone.utc)
        after_three = datetime(2026, 6, 5, 3, 30, tzinfo=timezone.utc)
        self.assertEqual(irrigation_prediction_scheduler.seconds_until_next_run(schedule_time, before_three), 30 * 60)
        self.assertEqual(irrigation_prediction_scheduler.seconds_until_next_run(schedule_time, after_three), 23.5 * 60 * 60)

    def test_config_route_persists_prediction_max_minutes(self):
        with self.client.session_transaction() as session:
            with self.app.app_context():
                user = User.query.filter_by(sub='ml-user').one()
                session['user_id'] = user.id

        response = self.client.post('/config/irrigation-prediction', data={'max_minutes': '75'})

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            config = InfluxIntegrationConfig.query.one()
            self.assertEqual(config.irrigation_prediction_max_minutes, 75)

    def test_widget_api_key_accepts_special_characters_via_bearer_header(self):
        special_token = "abc:!#%&=?{}[]_~.-+/\"'"
        self.app.config['WIDGET_API_KEY'] = special_token

        response = self.client.get('/api/stats', headers={
            'Authorization': f'Bearer {special_token}',
        })

        self.assertEqual(response.status_code, 200)

    def test_widget_api_key_accepts_special_characters_via_x_api_key_header(self):
        special_token = "abc:!#%&=?{}[]_~.-+/\"'"
        self.app.config['WIDGET_API_KEY'] = special_token

        response = self.client.get('/api/stats', headers={
            'X-API-Key': special_token,
        })

        self.assertEqual(response.status_code, 200)

    def test_api_returns_prediction_payload(self):
        now = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
        adapter = FakeAdapter({'soil': [{'time': now.isoformat(), 'value': 50}], 'irrigation': []})
        with self.app.app_context():
            db.session.add(IrrigationPredictionModel(
                location_id=self.location_id,
                trained_at=now,
                sample_count=3,
                intercept=200,
                coefficients_json='[0,0,0,0,0,0]',
                feature_names_json='[]',
                metrics_json='{}',
            ))
            db.session.commit()

        with patch('app.views._sensor_influx_config', return_value=SimpleNamespace(enabled=True)), \
             patch('app.views.influx_service.get_sensor_time_series_adapter', return_value=adapter):
            response = self.client.get('/api/locations/{}/irrigation-prediction'.format(self.location_id), headers={'X-API-Key': 'secret'})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['predicted_minutes'], 120)
        self.assertEqual(payload['max_minutes'], 120)


if __name__ == '__main__':
    unittest.main()
