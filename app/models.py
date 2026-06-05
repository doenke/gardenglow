from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc)


db = SQLAlchemy()

plant_light_need = db.Table(
    'plant_light_need',
    db.Column('plant_id', db.Integer, db.ForeignKey('plant.id'), primary_key=True),
    db.Column('light_need_id', db.Integer, db.ForeignKey('light_need.id'), primary_key=True),
)

plant_soil_property = db.Table(
    'plant_soil_property',
    db.Column('plant_id', db.Integer, db.ForeignKey('plant.id'), primary_key=True),
    db.Column('soil_property_id', db.Integer, db.ForeignKey('soil_property.id'), primary_key=True),
)

SENSOR_TYPE_SOIL_MOISTURE = 'soil_moisture'
SENSOR_TYPE_TEMPERATURE = 'temperature'
SENSOR_TYPE_RAINFALL = 'rainfall'
SENSOR_TYPE_IRRIGATION = 'irrigation'

SENSOR_TYPE_LABELS = {
    SENSOR_TYPE_SOIL_MOISTURE: 'Bodenfeuchte',
    SENSOR_TYPE_TEMPERATURE: 'Temperatur',
    SENSOR_TYPE_RAINFALL: 'Niederschlag',
    SENSOR_TYPE_IRRIGATION: 'Bewässerung',
}
SENSOR_TYPES = tuple(SENSOR_TYPE_LABELS.keys())

sensor_location = db.Table(
    'sensor_location',
    db.Column('sensor_id', db.Integer, db.ForeignKey('sensor.id'), primary_key=True),
    db.Column('location_id', db.Integer, db.ForeignKey('location.id'), primary_key=True),
)

class LightNeed(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(32), unique=True, nullable=False)
    label = db.Column(db.String(64), nullable=False)

class SoilProperty(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(128), unique=True, nullable=False)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sub = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255))
    email = db.Column(db.String(255))
    avatar_url = db.Column(db.String(1024))
    avatar_filename = db.Column(db.String(255))


class Location(db.Model):
    __table_args__ = (
        db.Index('ix_location_name', 'name'),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    color = db.Column(db.String(7), default='#2f6d40')
    polygon_points = db.Column(db.Text)
    target_soil_moisture_percent = db.Column(db.Float)


class Sensor(db.Model):
    __tablename__ = 'sensor'
    __table_args__ = (
        db.Index('ix_sensor_key', 'key'),
        db.Index('ix_sensor_sensor_type', 'sensor_type'),
        db.Index('ix_sensor_is_active', 'is_active'),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    key = db.Column(db.String(128), unique=True, nullable=False)
    sensor_type = db.Column(db.String(32), nullable=False, default=SENSOR_TYPE_SOIL_MOISTURE)
    homeassistant_entity_id = db.Column(db.String(255))
    influx_measurement = db.Column(db.String(255))
    influx_field = db.Column(db.String(255))
    influx_tags = db.Column(db.Text)
    map_x = db.Column(db.Float)
    map_y = db.Column(db.Float)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    locations = db.relationship(
        'Location',
        secondary=sensor_location,
        lazy='select',
        order_by='Location.name',
    )

    @property
    def type_label(self):
        return SENSOR_TYPE_LABELS.get(self.sensor_type, self.sensor_type or 'Sensor')


class InfluxIntegrationConfig(db.Model):
    __tablename__ = 'influx_integration_config'

    id = db.Column(db.Integer, primary_key=True)
    influx_url = db.Column(db.String(1024), nullable=False, default='')
    influx_org = db.Column(db.String(255), nullable=False, default='')
    influx_bucket = db.Column(db.String(255), nullable=False, default='')
    influx_token = db.Column(db.Text)
    homeassistant_url = db.Column(db.String(1024), nullable=False, default='')
    homeassistant_token = db.Column(db.Text)
    verify_tls = db.Column(db.Boolean, nullable=False, default=True)
    timeout_seconds = db.Column(db.Integer, nullable=False, default=10)
    target_soil_moisture_percent = db.Column(db.Float)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    @property
    def has_influx_token(self):
        return bool((self.influx_token or '').strip())

    @property
    def has_homeassistant_token(self):
        return bool((self.homeassistant_token or '').strip())


class Plant(db.Model):
    __table_args__ = (
        db.Index('ix_plant_location_id', 'location_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey('location.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    cultivar = db.Column(db.String(255))
    scientific_name = db.Column(db.String(255), index=True)
    common_name = db.Column(db.String(255))
    source = db.Column(db.String(255))
    light_needs = db.relationship('LightNeed', secondary=plant_light_need, lazy='select', order_by='LightNeed.id')
    bloom_start_month = db.Column(db.Integer)
    bloom_end_month = db.Column(db.Integer)
    flower_color = db.Column(db.String(64))
    soil_properties = db.relationship('SoilProperty', secondary=plant_soil_property, lazy='select', order_by='SoilProperty.label')
    height_without_bloom_cm = db.Column(db.Integer)
    height_with_bloom_cm = db.Column(db.Integer)
    info = db.Column(db.Text)
    map_x = db.Column(db.Float)
    map_y = db.Column(db.Float)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    database_identifiers = db.relationship(
        'PlantDatabaseIdentifier',
        back_populates='plant',
        lazy='select',
        cascade='all, delete-orphan',
        order_by='PlantDatabaseIdentifier.id',
    )

    @property
    def soil_property_labels(self):
        return [soil_property.label for soil_property in self.soil_properties]


class GardenMap(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255))
    calibration_points = db.Column(db.Text)
    boundary_points = db.Column(db.Text)


class PlantDatabaseIdentifier(db.Model):
    __table_args__ = (
        db.UniqueConstraint('plant_id', 'catalog_key', name='ux_plant_database_identifier_plant_catalog'),
    )

    id = db.Column(db.Integer, primary_key=True)
    plant_id = db.Column(db.Integer, db.ForeignKey('plant.id'), nullable=False, index=True)
    catalog_key = db.Column(db.String(64), nullable=False, index=True)
    taxonomy_id = db.Column(db.String(255), nullable=False)
    plant = db.relationship('Plant', back_populates='database_identifiers')

    @property
    def catalog(self):
        from .taxonomy.catalogs import get_database_catalog_by_key

        return get_database_catalog_by_key(self.catalog_key)


class PlantPhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plant_id = db.Column(db.Integer, db.ForeignKey('plant.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    taken_on = db.Column(db.Date)
    comment = db.Column(db.Text)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


class PlantNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plant_id = db.Column(db.Integer, db.ForeignKey('plant.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    note_date = db.Column(db.Date, nullable=False)
    comment = db.Column(db.Text, nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


class TimelineEntry(db.Model):
    __table_args__ = (
        db.Index('ix_timeline_entry_scope_created_at', 'scope_type', 'scope_id', db.desc('created_at')),
        db.Index('ix_timeline_entry_scope_title_entry', 'scope_type', 'scope_id', 'is_title_entry'),
        db.Index(
            'ux_timeline_entry_single_title_per_scope',
            'scope_type',
            'scope_id',
            unique=True,
            sqlite_where=text('is_title_entry = 1'),
            postgresql_where=text('is_title_entry IS TRUE'),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    scope_type = db.Column(db.String(32), nullable=False, index=True)
    scope_id = db.Column(db.Integer, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    event_at = db.Column(db.DateTime(timezone=True))
    event_type = db.Column(db.String(32))
    title = db.Column(db.String(255))
    description = db.Column(db.Text)
    comment = db.Column(db.Text)
    attachment_filename = db.Column(db.String(255))
    attachment_kind = db.Column(db.String(16))
    is_title_entry = db.Column(db.Boolean, nullable=False, default=False, index=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
