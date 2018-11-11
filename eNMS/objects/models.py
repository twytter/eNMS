from re import search
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Float
from sqlalchemy.orm import backref, relationship

from eNMS.base.associations import (
    pool_device_table,
    pool_link_table,
    job_device_table,
    job_pool_table
)
from eNMS.base.helpers import fetch, get_one
from eNMS.base.models import Base
from eNMS.base.properties import (
    custom_properties,
    link_public_properties,
    device_public_properties,
    sql_types
)
from eNMS.objects.helpers import database_filtering


class Object(Base):

    __tablename__ = 'Object'
    __mapper_args__ = {
        'polymorphic_identity': 'Object',
        'polymorphic_on': type
    }
    id = Column(Integer, primary_key=True)
    hidden = Column(Boolean, default=False)
    name = Column(String, unique=True)
    subtype = Column(String)
    description = Column(String)
    model = Column(String)
    location = Column(String)
    vendor = Column(String)
    type = Column(String)


CustomDevice = type('CustomDevice', (Object,), {
    '__tablename__': 'CustomDevice',
    '__mapper_args__': {'polymorphic_identity': 'CustomDevice'},
    'id': Column(Integer, ForeignKey('Object.id'), primary_key=True),
    **{
        property: Column(sql_types[values['type']], default=values['default'])
        for property, values in custom_properties.items()
    }
}) if custom_properties else Object


class Device(CustomDevice):

    __tablename__ = 'Device'
    __mapper_args__ = {'polymorphic_identity': 'Device'}
    id = Column(Integer, ForeignKey(CustomDevice.id), primary_key=True)
    operating_system = Column(String)
    os_version = Column(String)
    ip_address = Column(String)
    longitude = Column(Float)
    latitude = Column(Float)
    port = Column(Integer, default=22)
    username = Column(String)
    password = Column(String)
    enable_password = Column(String)
    jobs = relationship(
        'Job',
        secondary=job_device_table,
        back_populates='devices'
    )
    pools = relationship(
        'Pool',
        secondary=pool_device_table,
        back_populates='devices'
    )

    class_type = 'device'


class Link(Object):

    __tablename__ = 'Link'
    __mapper_args__ = {'polymorphic_identity': 'Link'}
    id = Column(Integer, ForeignKey('Object.id'), primary_key=True)
    source_id = Column(Integer, ForeignKey('Device.id'))
    destination_id = Column(Integer, ForeignKey('Device.id'))
    source = relationship(
        Device,
        primaryjoin=source_id == Device.id,
        backref=backref('source', cascade='all, delete-orphan')
    )
    destination = relationship(
        Device,
        primaryjoin=destination_id == Device.id,
        backref=backref('destination', cascade='all, delete-orphan')
    )
    pools = relationship(
        'Pool',
        secondary=pool_link_table,
        back_populates='links'
    )

    def __init__(self, **kwargs):
        self.update(**kwargs)

    def update(self, **kwargs):
        if 'source_name' in kwargs:
            source = fetch('Device', name=kwargs.pop('source_name'))
            destination = fetch('Device', name=kwargs.pop('destination_name'))
            kwargs.update({
                'source_id': source.id,
                'destination_id': destination.id,
                'source': source.id,
                'destination': destination.id
            })
        super().update(**kwargs)

    @property
    def source_name(self):
        return self.source.name

    @property
    def destination_name(self):
        return self.destination.name

    class_type = 'link'


AbstractPool = type('AbstractPool', (Base,), {
    '__tablename__': 'AbstractPool',
    '__mapper_args__': {'polymorphic_identity': 'AbstractPool'},
    'id': Column(Integer, primary_key=True), **{
        **{f'device_{p}': Column(String) for p in device_public_properties},
        **{
            f'device_{p}_regex': Column(Boolean)
            for p in device_public_properties
        },
        **{f'link_{p}': Column(String) for p in link_public_properties},
        **{f'link_{p}_regex': Column(Boolean) for p in link_public_properties}
    }
})


class Pool(AbstractPool):

    __tablename__ = 'Pool'
    __mapper_args__ = {'polymorphic_identity': 'Pool'}
    id = Column(Integer, ForeignKey('AbstractPool.id'), primary_key=True)
    name = Column(String, unique=True)
    description = Column(String)
    devices = relationship(
        'Device',
        secondary=pool_device_table,
        back_populates='pools'
    )
    links = relationship(
        'Link',
        secondary=pool_link_table,
        back_populates='pools'
    )
    jobs = relationship(
        'Job',
        secondary=job_pool_table,
        back_populates='pools'
    )

    def update(self, **kwargs):
        super().update(**kwargs)
        self.compute_pool()

    def compute_pool(self):
        self.devices = list(filter(self.object_match, Device.query.all()))
        self.links = []
        for link in Link.query.all():
            link.__dict__.update({
                'source': link.source,
                'destination': link.destination
            })
            if self.object_match(link):
                self.links.append(link)
        if get_one('Parameters').pool == self:
            database_filtering(self)

    def object_match(self, obj):
        return all(
            # if the device-regex property is not in the request, the
            # regex box is unticked and we only check that the values
            # are equal.
            str(value) == getattr(self, f'{obj.class_type}_{prop}')
            if not getattr(self, f'{obj.class_type}_{prop}_regex')
            # if it is ticked, we use re.search to check that the value
            # of the device property matches the regular expression.
            else search(getattr(self, f'{obj.class_type}_{prop}'), str(value))
            for prop, value in obj.__dict__.items()
            # we consider only the properties in the form
            if f'{obj.class_type}_{prop}' in self.__dict__
            # providing that the property field in the form is not empty
            # (empty field <==> property ignored)
            and getattr(self, f'{obj.class_type}_{prop}')
        )

    def filter_objects(self):
        return {
            'devices': [device.serialized for device in self.devices],
            'links': [link.serialized for link in self.links]
        }
