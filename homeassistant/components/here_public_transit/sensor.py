"""Support for HERE Public Transit sensors."""
from datetime import datetime, timedelta
import logging
from typing import Callable, Dict, Optional, Union

import herepy
import voluptuous as vol
import isodate

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_MODE,
    CONF_MODE,
    CONF_NAME,
    CONF_UNIT_SYSTEM,
    CONF_UNIT_SYSTEM_IMPERIAL,
    CONF_UNIT_SYSTEM_METRIC,
    EVENT_HOMEASSISTANT_START,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import location
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
import homeassistant.util.dt as dt

_LOGGER = logging.getLogger(__name__)

CONF_DESTINATION_LATITUDE = "destination_latitude"
CONF_DESTINATION_LONGITUDE = "destination_longitude"
CONF_DESTINATION_ENTITY_ID = "destination_entity_id"
CONF_ORIGIN_LATITUDE = "origin_latitude"
CONF_ORIGIN_LONGITUDE = "origin_longitude"
CONF_ORIGIN_ENTITY_ID = "origin_entity_id"
CONF_API_KEY = "api_key"
CONF_ARRIVAL = "arrival"
CONF_DEPARTURE = "departure"

DEFAULT_NAME = "HERE Public Transit"

UNITS = [CONF_UNIT_SYSTEM_METRIC, CONF_UNIT_SYSTEM_IMPERIAL]

ATTR_DURATION = "duration"
ATTR_ROUTE = "route"
ATTR_ORIGIN = "origin"
ATTR_DESTINATION = "destination"

ATTR_UNIT_SYSTEM = CONF_UNIT_SYSTEM

ATTR_DURATION_IN_TRAFFIC = "duration_in_traffic"
ATTR_ORIGIN_NAME = "origin_name"
ATTR_DESTINATION_NAME = "destination_name"

UNIT_OF_MEASUREMENT = "min"

SCAN_INTERVAL = timedelta(minutes=5)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_API_KEY): cv.string,
        vol.Inclusive(
            CONF_DESTINATION_LATITUDE, "destination_coordinates"
        ): cv.latitude,
        vol.Inclusive(
            CONF_DESTINATION_LONGITUDE, "destination_coordinates"
        ): cv.longitude,
        vol.Exclusive(CONF_DESTINATION_LATITUDE, "destination"): cv.latitude,
        vol.Exclusive(CONF_DESTINATION_ENTITY_ID, "destination"): cv.entity_id,
        vol.Inclusive(CONF_ORIGIN_LATITUDE, "origin_coordinates"): cv.latitude,
        vol.Inclusive(CONF_ORIGIN_LONGITUDE, "origin_coordinates"): cv.longitude,
        vol.Exclusive(CONF_ORIGIN_LATITUDE, "origin"): cv.latitude,
        vol.Exclusive(CONF_ORIGIN_ENTITY_ID, "origin"): cv.entity_id,
        vol.Exclusive(CONF_ARRIVAL, "arrival_departure"): cv.time,
        vol.Exclusive(CONF_DEPARTURE, "arrival_departure"): cv.time,
        vol.Optional(CONF_DEPARTURE, default="now"): cv.time,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_MODE, default=TRAVEL_MODE_CAR): vol.In(TRAVEL_MODE),
        vol.Optional(CONF_ROUTE_MODE, default=ROUTE_MODE_FASTEST): vol.In(ROUTE_MODE),
        vol.Optional(CONF_TRAFFIC_MODE, default=False): cv.boolean,
        vol.Optional(CONF_UNIT_SYSTEM): vol.In(UNITS),
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: Dict[str, Union[str, bool]],
    async_add_entities: Callable,
    discovery_info: None = None,
) -> None:
    """Set up the HERE travel time platform."""

    api_key = config[CONF_API_KEY]
    here_client = herepy.RoutingApi(api_key)

    if not await hass.async_add_executor_job(
        _are_valid_client_credentials, here_client
    ):
        _LOGGER.error(
            "Invalid credentials. This error is returned if the specified token was invalid or no contract could be found for this token."
        )
        return

    if config.get(CONF_ORIGIN_LATITUDE) is not None:
        origin = f"{config[CONF_ORIGIN_LATITUDE]},{config[CONF_ORIGIN_LONGITUDE]}"
        origin_entity_id = None
    else:
        origin = None
        origin_entity_id = config[CONF_ORIGIN_ENTITY_ID]

    if config.get(CONF_DESTINATION_LATITUDE) is not None:
        destination = (
            f"{config[CONF_DESTINATION_LATITUDE]},{config[CONF_DESTINATION_LONGITUDE]}"
        )
        destination_entity_id = None
    else:
        destination = None
        destination_entity_id = config[CONF_DESTINATION_ENTITY_ID]

    travel_mode = config[CONF_MODE]
    name = config[CONF_NAME]
    units = config.get(CONF_UNIT_SYSTEM, hass.config.units.name)

    here_data = HERETravelTimeData(
        here_client, travel_mode, traffic_mode, route_mode, units
    )

    sensor = HERETravelTimeSensor(
        name, origin, destination, origin_entity_id, destination_entity_id, here_data
    )

    async_add_entities([sensor])


def _are_valid_client_credentials(here_client: herepy.PublicTransitApi) -> bool:
    """Check if the provided credentials are correct using defaults."""
    known_working_origin = [38.9, -77.04833]
    known_working_destination = [39.0, -77.1]
    try:
        here_client.calculate_route(
            known_working_origin,
            known_working_destination,
        )
    except herepy.InvalidCredentialsError:
        return False
    return True


class HEREPublicTransitSensor(Entity):
    """Representation of a HERE travel time sensor."""

    def __init__(
        self,
        name: str,
        origin: str,
        destination: str,
        origin_entity_id: str,
        destination_entity_id: str,
        here_data: "HEREPublicTransitData",
    ) -> None:
        """Initialize the sensor."""
        self._name = name
        self._origin_entity_id = origin_entity_id
        self._destination_entity_id = destination_entity_id
        self._here_data = here_data
        self._unit_of_measurement = UNIT_OF_MEASUREMENT
        self._attrs = {
            ATTR_UNIT_SYSTEM: self._here_data.units,
        }
        if self._origin_entity_id is None:
            self._here_data.origin = origin

        if self._destination_entity_id is None:
            self._here_data.destination = destination

    async def async_added_to_hass(self) -> None:
        """Delay the sensor update to avoid entity not found warnings."""

        @callback
        def delayed_sensor_update(event):
            """Update sensor after homeassistant started."""
            self.async_schedule_update_ha_state(True)

        self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_START, delayed_sensor_update
        )

    @property
    def state(self) -> Optional[str]:
        """Return the state of the sensor."""
        if self._here_data.duration is not None:
            return str(round(self._here_data.duration / 60))
        return None

    @property
    def name(self) -> str:
        """Get the name of the sensor."""
        return self._name

    @property
    def device_state_attributes(
        self,
    ) -> Optional[Dict[str, Union[None, float, str, bool]]]:
        """Return the state attributes."""
        if self._here_data.duration is None:
            return None

        res = self._attrs
        if self._here_data.attribution is not None:
            res[ATTR_ATTRIBUTION] = self._here_data.attribution
        res[ATTR_ROUTE] = self._here_data.route
        res[ATTR_ORIGIN] = self._here_data.origin
        res[ATTR_DESTINATION] = self._here_data.destination
        res["arrival"] = self._here_data.arrival
        res["departure"] = self._here_data.departure
        res["is_realtime"] = self._here_data.is_realtime
        res["transfers"] = self._here_data.transfers
        return res

    @property
    def unit_of_measurement(self) -> str:
        """Return the unit this state is expressed in."""
        return self._unit_of_measurement

    @property
    def icon(self) -> str:
        """Icon to use in the frontend depending on travel_mode."""
        if self._here_data.travel_mode == TRAVEL_MODE_BICYCLE:
            return ICON_BICYCLE
        if self._here_data.travel_mode == TRAVEL_MODE_PEDESTRIAN:
            return ICON_PEDESTRIAN
        if self._here_data.travel_mode in TRAVEL_MODES_PUBLIC:
            return ICON_PUBLIC
        if self._here_data.travel_mode == TRAVEL_MODE_TRUCK:
            return ICON_TRUCK
        return ICON_CAR

    async def async_update(self) -> None:
        """Update Sensor Information."""
        # Convert device_trackers to HERE friendly location
        if self._origin_entity_id is not None:
            self._here_data.origin = await self._get_location_from_entity(
                self._origin_entity_id
            )

        if self._destination_entity_id is not None:
            self._here_data.destination = await self._get_location_from_entity(
                self._destination_entity_id
            )

        await self.hass.async_add_executor_job(self._here_data.update)


class HEREPublicTransitData:
    """HEREPublicTransit data object."""

    def __init__(
        self,
        here_client: herepy.PublicTransitApi,
        time: str,
        max_connections: int,
        max_changes: int,
        lang: str,
        include_modes: list,
        exclude_modes: list,
        units: str,
        max_walking_distance: int,
        walking_speed: int,
        show_arrival_times: bool,
    ) -> None:
        """Initialize herepy."""
        self.origin = None
        self.destination = None
        self.time = None
        self.attribution = None
        self.is_realtime = False
        self.route = None
        self.duration = None
        self.departure = None
        self.arrival = None
        self.is_realtime = False
        self.transfers = 0
        self.time = time
        self.max_connections = max_connections
        self.max_changes = max_changes
        self.lang = lang
        self.include_modes = include_modes
        self.exclude_modes = exclude_modes
        self.units =  units
        self.max_walking_distance = max_walking_distance
        self.walking_speed = walking_speed
        self.show_arrival_times = show_arrival_times
        self._client = here_client

    def update(self) -> None:
        """Get the latest data from HERE."""

        if self.destination is not None and self.origin is not None:
            # Convert location to HERE friendly location
            destination = self.destination.split(",")
            origin = self.origin.split(",")
            time = convert_time_to_isodate(self.time)

            _LOGGER.debug(
                "Requesting route for origin: %s, destination: %s, time: %s, max_connections: %s, max_changes: %s, lang: %s, include_modes: %s, exclude_modes: %s, units: %s, max_walking_distance: %s, walking_speed: %s, show_arrival_times: %s",
                origin,
                destination,
                time,
                self.max_connections,
                self.max_changes,
                self.lang,
                self.include_modes,
                self.exclude_modes,
                self.units,
                self.max_walking_distance,
                self.walking_speed,
                self.show_arrival_times,
            )
            response = self._client.calculate_route(
                origin,
                destination,
                time,
                self.max_connections,
                self.max_changes,
                self.lang,
                self.include_modes,
                self.exclude_modes,
                self.units,
                self.max_walking_distance,
                self.walking_speed,
                self.show_arrival_times,
                False,
                herepy.PublicTransitRoutingMode.realtime
            )


            _LOGGER.debug("Raw response is: %s", response.Res)

            # pylint: disable=no-member
            source_attribution = response.response.get("sourceAttribution")
            if source_attribution is not None:
                self.attribution = self._build_hass_attribution(source_attribution)
            # pylint: disable=no-member
            connection = response.Res["Connections"]["Connection"][0]
            iso_duration = isodate.parse_duration(connection["duration"])
            self.duration = iso_duration.total_seconds() // 60

            rt_aware = connection.get("rt_aware")
            if rt_aware is not None:
                self.is_realtime = rt_aware
            self.transfers = connection["transfers"]
            self.destination = connection["Dep"]["time"]
            self.arrival = connection["Arr"]["time"]
            self.route = connection["short_route"]

    @staticmethod
    def _build_hass_attribution(source_attribution: Dict) -> Optional[str]:
        """Build a hass frontend ready string out of the sourceAttribution."""
        suppliers = source_attribution.get("supplier")
        if suppliers is not None:
            supplier_titles = []
            for supplier in suppliers:
                title = supplier.get("title")
                if title is not None:
                    supplier_titles.append(title)
            joined_supplier_titles = ",".join(supplier_titles)
            attribution = f"With the support of {joined_supplier_titles}. All information is provided without warranty of any kind."
            return attribution


def convert_time_to_isodate(timestr: str) -> str:
    """Take a string like 08:00:00 and combine it with the current date."""
    combined = datetime.combine(dt.start_of_local_day(), dt.parse_time(timestr))
    if combined < datetime.now():
        combined = combined + timedelta(days=1)
    return combined.isoformat()
