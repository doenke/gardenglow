import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import InfluxIntegrationConfig, Location, Plant, Sensor, TimelineEntry, User, db, SENSOR_TYPE_TEMPERATURE


class LocationTimelineViewTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.close(self.db_fd)
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            self.user = User(sub='test-user', name='Test User')
            db.session.add(self.user)
            db.session.flush()
            self.location = Location(name='Sonnenbeet')
            db.session.add(self.location)
            db.session.flush()
            self.plant = Plant(name='Salbei', location_id=self.location.id, creator_id=self.user.id)
            db.session.add(self.plant)
            db.session.commit()
            self.user_id = self.user.id
            self.location_id = self.location.id
            self.plant_id = self.plant.id

        with self.client.session_transaction() as session:
            session['user_id'] = self.user_id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)

    def test_location_timeline_uses_event_form_layout_and_renders_title(self):
        response = self.client.get(f'/locations/{self.location_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<section class="timeline-panel">', html)
        self.assertIn('<h4 class="timeline-toolbar-title">Neues Event</h4>', html)
        self.assertIn('id="location-timeline-title"', html)
        self.assertIn('class="grid timeline-form"', html)

        response = self.client.post(
            f'/locations/{self.location_id}/timeline/new',
            data={'title': 'Beet vorbereitet', 'description': 'Kompost eingearbeitet'},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<strong>Beet vorbereitet</strong>', html)
        self.assertIn('Kompost eingearbeitet', html)
        with self.app.app_context():
            entry = TimelineEntry.query.filter_by(scope_type='location', scope_id=self.location_id).one()
            self.assertEqual(entry.title, 'Beet vorbereitet')
            self.assertEqual(entry.description, 'Kompost eingearbeitet')

    def test_location_weather_chart_renders_daily_temperature_range_area(self):
        with self.app.app_context():
            location = db.session.get(Location, self.location_id)
            sensor = Sensor(
                name='Außentemperatur',
                key='outside_temperature',
                sensor_type=SENSOR_TYPE_TEMPERATURE,
                creator_id=self.user_id,
            )
            sensor.locations.append(location)
            db.session.add(sensor)
            db.session.commit()

        response = self.client.get(f'/locations/{self.location_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('const dailyTemperatureRange = (points) => {', html)
        self.assertIn('const drawTemperatureRangeArea = (context, ranges, { xFor, yFor }) => {', html)
        self.assertIn('drawTemperatureRangeArea(context, temperatureRanges', html)
        self.assertIn('data-chart-height="260" data-chart-height-mobile="180"', html)
        self.assertIn('const desktopHeight = Number(canvas.dataset.chartHeight) || 260;', html)
        self.assertIn('const mobileHeight = Number(canvas.dataset.chartHeightMobile) || 180;', html)
        self.assertNotIn("const defaultHeight = Number(canvas.getAttribute('height')) || 260;", html)
        self.assertNotIn('drawLineSeries(context, sensorSeries.parsedPoints, {\n          xFor,\n          yFor: temperatureYFor', html)

    def test_location_timeline_allows_text_only_without_file_warning(self):
        response = self.client.post(
            f'/locations/{self.location_id}/timeline/new',
            data={'description': 'Nur Text, keine Datei'},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Nur Text, keine Datei', html)
        self.assertNotIn('Bitte Datei auswählen', html)
        with self.app.app_context():
            entry = TimelineEntry.query.filter_by(scope_type='location', scope_id=self.location_id).one()
            self.assertIsNone(entry.title)
            self.assertEqual(entry.description, 'Nur Text, keine Datei')
            self.assertIsNone(entry.attachment_filename)

    def test_location_timeline_allows_title_only(self):
        response = self.client.post(
            f'/locations/{self.location_id}/timeline/new',
            data={'title': 'Nur Titel'},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<strong>Nur Titel</strong>', html)
        self.assertNotIn('Bitte Datei auswählen', html)
        with self.app.app_context():
            entry = TimelineEntry.query.filter_by(scope_type='location', scope_id=self.location_id).one()
            self.assertEqual(entry.title, 'Nur Titel')
            self.assertIsNone(entry.description)
            self.assertIsNone(entry.attachment_filename)

    def test_location_timeline_rejects_empty_entries_once(self):
        response = self.client.post(
            f'/locations/{self.location_id}/timeline/new',
            data={'title': '   ', 'description': '   '},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Bitte Titel, Beschreibung oder Datei angeben.', html)
        self.assertNotIn('Bitte Datei auswählen', html)
        with self.app.app_context():
            self.assertEqual(TimelineEntry.query.filter_by(scope_type='location', scope_id=self.location_id).count(), 0)

    def test_plant_timeline_allows_title_only_and_normalizes_missing_description(self):
        response = self.client.post(
            f'/plants/{self.plant_id}/events',
            data={'title': 'Nur Pflanzentitel'},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<strong>Nur Pflanzentitel</strong>', html)
        self.assertNotIn('Bitte Datei auswählen', html)
        with self.app.app_context():
            entry = TimelineEntry.query.filter_by(scope_type='plant', scope_id=self.plant_id).one()
            self.assertEqual(entry.title, 'Nur Pflanzentitel')
            self.assertIsNone(entry.description)
            self.assertIsNone(entry.attachment_filename)

    def test_location_soil_moisture_target_overrides_global_target(self):
        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(target_soil_moisture_percent=50))
            db.session.commit()

        response = self.client.get(f'/locations/{self.location_id}')
        html = response.get_data(as_text=True)
        self.assertIn('Aktive Ziellinie: 50 % · Globale Vorgabe', html)
        self.assertNotIn('id="target-soil-moisture-data"', html)
        self.assertNotIn('id="soil-moisture-chart"', html)
        self.assertNotIn('Sensor-Verlauf', html)
        self.assertNotIn('Wetterdaten-Verlauf', html)

        response = self.client.post(
            f'/locations/{self.location_id}/target-soil-moisture',
            data={'target_soil_moisture_percent': '62.5'},
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.get(f'/locations/{self.location_id}/soil-moisture')
        payload = response.get_json()
        self.assertEqual(payload['target_soil_moisture']['value'], 62.5)
        self.assertEqual(payload['target_soil_moisture']['source'], 'location')

    def test_location_soil_moisture_target_can_fall_back_to_global(self):
        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(target_soil_moisture_percent=47))
            db.session.commit()

        self.client.post(
            f'/locations/{self.location_id}/target-soil-moisture',
            data={'target_soil_moisture_percent': '61'},
        )
        self.client.post(
            f'/locations/{self.location_id}/target-soil-moisture',
            data={'target_soil_moisture_percent': ''},
        )

        response = self.client.get(f'/locations/{self.location_id}/soil-moisture')
        payload = response.get_json()
        self.assertEqual(payload['target_soil_moisture']['value'], 47)
        self.assertEqual(payload['target_soil_moisture']['source'], 'global')

if __name__ == '__main__':
    unittest.main()

