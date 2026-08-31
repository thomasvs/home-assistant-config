from datetime import timedelta
import logging
import voluptuous as vol

from homeassistant.auth.models import RefreshToken
from homeassistant.components import media_source
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaType,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.helpers import entity_platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url

try:
    STATE_PLAYING = MediaPlayerState.PLAYING
    STATE_PAUSED = MediaPlayerState.PAUSED
    STATE_OFF = MediaPlayerState.OFF
except AttributeError:
    from homeassistant.const import STATE_PLAYING, STATE_PAUSED, STATE_OFF

MEDIA_CLASS_DIRECTORY = MediaClass.DIRECTORY
MEDIA_TYPE_IMAGE = MediaType.IMAGE
MEDIA_TYPE_PLAYLIST = MediaType.PLAYLIST

from .const import DOMAIN
from .pymeural import LocalMeural

_LOGGER = logging.getLogger(__name__)

MEURAL_SUPPORT = (
    MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.TURN_ON
)

async def async_setup_entry(hass, config_entry, async_add_entities):
    meural = hass.data[DOMAIN][config_entry.entry_id]
    try:
        devices = await meural.get_user_devices()
    except Exception as err:
        _LOGGER.warning("Meural: Cloud authentication/lookup failed (%s). Initializing local device mode.", err)
        devices = [{
            "id": 1,
            "alias": "living-room-mural",
            "name": "living-room-mural",
            "localIp": "192.168.1.109",
            "productKey": "MEU0118020001701",
            "imageDuration": 300,
            "orientation": "portrait",
            "imageShuffle": False,
            "status": "online",
            "version": "2.0.8",
            "frameModel": {"name": "Canvas I Leonora black"},
        }]
    for device in devices:
        _LOGGER.info("Adding Meural device %s" % (device['alias'], ))
        async_add_entities([MeuralEntity(meural, device)], True)

    platform = entity_platform.current_platform.get()

    platform.async_register_entity_service(
        "set_brightness",
        {
            vol.Required("brightness"): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=100)
            )
        },
        "async_set_brightness",
    )

    platform.async_register_entity_service(
        "preview_image",
        {
            vol.Required("content_url"): str,
            vol.Required("content_type"): str,
        },
        "async_preview_image",
    )

    platform.async_register_entity_service(
        "reset_brightness",
        {},
        "async_reset_brightness",
    )

    platform.async_register_entity_service(
        "toggle_informationcard",
        {},
        "async_toggle_informationcard",
    )

    platform.async_register_entity_service(
        "set_device_option",
        {
            vol.Optional("orientation"): str,
            vol.Optional("orientationMatch"): bool,
            vol.Optional("alsEnabled"): bool,
            vol.Optional("alsSensitivity"): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=100)
            ),
            vol.Optional("goesDark"): bool,
            vol.Optional("imageShuffle"): bool,
            vol.Optional("imageDuration"): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=86400)
            ),
            vol.Optional("previewDuration"): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=86400)
            ),
            vol.Optional("overlayDuration"): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=86400)
            ),
            vol.Optional("gestureFeedback"): bool,
            vol.Optional("gestureFeedbackHelp"): bool,
            vol.Optional("gestureFlip"): bool,
            vol.Optional("backgroundColor"): str,
            vol.Optional("fillMode"): str,
            vol.Optional("schedulerEnabled"): bool,
            vol.Optional("galleryRotation"): bool
        },
        "async_set_device_option",
    )

    platform.async_register_entity_service(
        "synchronize",
        {},
        "async_synchronize",
    )

class MeuralEntity(MediaPlayerEntity):
    """Representation of a Meural entity."""

    def __init__(self, meural, device):
        self.meural = meural
        self._meural_device = device
        self._galleries = []
        self._remote_galleries = []
        self._gallery_status = {}
        self._current_item = {}

        self._pause_duration = 0
        self._sleep = False
        self._abort = False

    @property
    def meural_device_id(self):
        return self._meural_device.get("id", 1)

    @property
    def meural_device_name(self):
        return self._meural_device.get("name", "living-room-mural")

    @property
    def local_meural(self):
        return LocalMeural(
            self._meural_device,
            async_get_clientsession(self.hass),
        )

    async def async_added_to_hass(self):
        """Set up default image duration."""
        try:
            _LOGGER.info("Meural device %s: Setup. Getting device information from Meural server", self.name)
            self._meural_device = await self.meural.get_device(self.meural_device_id)
            self._pause_duration = self._meural_device.get("imageDuration", 300)
        except Exception:
            _LOGGER.warning("Meural device %s: Could not contact Meural server, using local parameters", self.name)
            self._pause_duration = self._meural_device.get("imageDuration", 300)

        """Set up local galleries."""
        try:
            localgalleries = await self.local_meural.send_get_galleries()
            self._galleries = sorted(localgalleries, key = lambda i: i["name"])
            _LOGGER.info("Meural device %s: Setup. Has %d local galleries on local device" % (self.name, len(self._galleries)))
        except Exception:
            _LOGGER.error("Meural device %s: Setup. Error while contacting local device", self.name, exc_info=True)
            self._abort = True
            return

        """Set up remote galleries."""
        try:
            device_galleries = await self.meural.get_device_galleries(self.meural_device_id)
            user_galleries = await self.meural.get_user_galleries()
            [device_galleries.append(x) for x in user_galleries if x not in device_galleries]
            self._remote_galleries = device_galleries
            _LOGGER.info("Meural device %s: Setup. Has %d unique remote galleries on Meural server" % (self.name, len(self._remote_galleries)))
        except Exception:
            self._remote_galleries = []

        """Fetch current gallery and item status directly from local frame."""
        try:
            self._gallery_status = await self.local_meural.send_get_gallery_status()
            current_gallery = self._gallery_status.get("current_gallery")
            current_item_id = str(self._gallery_status.get("current_item"))
            if current_gallery:
                items = await self.local_meural.send_get_items_by_gallery(current_gallery)
                for itm in items:
                    if str(itm.get("id")) == current_item_id:
                        self._current_item = dict(itm)
                        break
                if current_item_id:
                    try:
                        cloud_item = await self.meural.get_item(int(current_item_id))
                        if cloud_item:
                            for k, v in cloud_item.items():
                                if v and not self._current_item.get(k):
                                    self._current_item[k] = v
                            if cloud_item.get("image"):
                                self._current_item["image"] = cloud_item["image"]
                    except Exception:
                        pass
        except Exception as err:
            _LOGGER.warning("Meural device %s: Setup error getting local item info: %s", self.name, err)

        _LOGGER.info("Meural device %s: Setup has completed",  self.name)

    async def async_update(self):
        if self._abort:
            _LOGGER.debug("Meural device %s: Updating. Setup was aborted, device will not be updated", self.name)
            return

        try:
            self._sleep = await self.local_meural.send_get_sleep()
        except Exception:
            self._sleep = False

        if not self._sleep:
            try:
                localgalleries = await self.local_meural.send_get_galleries()
                self._galleries = sorted(localgalleries, key = lambda i: i["name"])
            except Exception:
                pass

            try:
                self._gallery_status = await self.local_meural.send_get_gallery_status()
                current_gallery = self._gallery_status.get("current_gallery")
                current_item_id = str(self._gallery_status.get("current_item"))
                if current_gallery:
                    items = await self.local_meural.send_get_items_by_gallery(current_gallery)
                    for itm in items:
                        if str(itm.get("id")) == current_item_id:
                            self._current_item = dict(itm)
                            break
                    if current_item_id:
                        try:
                            cloud_item = await self.meural.get_item(int(current_item_id))
                            if cloud_item:
                                for k, v in cloud_item.items():
                                    if v and not self._current_item.get(k):
                                        self._current_item[k] = v
                                if cloud_item.get("image"):
                                    self._current_item["image"] = cloud_item["image"]
                        except Exception:
                            pass
            except Exception as err:
                _LOGGER.warning("Meural device %s: Error polling local frame: %s", self.name, err)

    @property
    def name(self):
        """Name of the device."""
        return self._meural_device.get("alias", "living-room-mural")

    @property
    def unique_id(self):
        """Unique ID of the device."""
        return self._meural_device.get("productKey", "MEU0118020001701")

    @property
    def device_info(self):
        return {
            "identifiers": {
                (DOMAIN, self.unique_id)
            },
            "name": self.name,
            "manufacturer": "NETGEAR",
            "model": self._meural_device.get("frameModel", {}).get("name", "Canvas I Leonora black"),
            "sw_version": self._meural_device.get("version", "2.0.8"),
            "configuration_url": "http://" + self._meural_device.get("localIp", "192.168.1.109") + "/remote/",
        }

    @property
    def available(self):
        """Device available."""
        return self._meural_device.get("status") != "offline"

    @property
    def state(self):
        """Return the state of the entity."""
        if self._sleep:
            return STATE_OFF
        elif self._meural_device.get("imageDuration") == 0:
            return STATE_PAUSED
        return STATE_PLAYING

    @property
    def source(self):
        """Name of the current playlist."""
        if isinstance(self._gallery_status, dict):
            return self._gallery_status.get("current_gallery_name") or str(self._gallery_status.get("current_gallery"))
        return None

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return MEURAL_SUPPORT

    @property
    def source_list(self):
        """List of available playlists."""
        return [g["name"] for g in self._galleries]

    @property
    def media_content_id(self):
        """Return the content ID of current playing media."""
        if isinstance(self._gallery_status, dict) and "current_item" in self._gallery_status:
            return str(self._gallery_status["current_item"])
        return None

    @property
    def media_content_type(self):
        """Return the content type of current playing media."""
        return MEDIA_TYPE_IMAGE

    @property
    def media_summary(self):
        """Return the summary of current playing media."""
        if not self._current_item:
            return None
        return self._current_item.get("description")

    @property
    def media_title(self):
        """Return the title of current playing media."""
        if not self._current_item:
            return None
        return self._current_item.get("title") or self._current_item.get("name")

    @property
    def media_artist(self):
        """Artist of current playing media."""
        if not self._current_item:
            return None
        artist = self._current_item.get("artistName") or self._current_item.get("author") or self._current_item.get("curator") or self._current_item.get("partner")
        year = self._current_item.get("year")
        if artist and year:
            return f"{artist}, {year}"
        return artist or (f"Unknown, {year}" if year else None)

    @property
    def media_image_url(self):
        """Image url of current playing media."""
        if self._current_item:
            if self._current_item.get("image"):
                return self._current_item["image"]
            if self._current_item.get("src"):
                src = self._current_item["src"]
                if src.startswith("http"):
                    return src
                elif src:
                    return f"http://{self.local_meural.ip}{src if src.startswith('/') else '/' + src}"

        # Fallback to active gallery cover image
        if isinstance(self._gallery_status, dict):
            current_gallery_id = str(self._gallery_status.get("current_gallery"))
            for gal in self._galleries:
                if str(gal.get("id")) == current_gallery_id and gal.get("src"):
                    src = gal["src"]
                    if src.startswith("http"):
                        return src
                    elif src:
                        return f"http://{self.local_meural.ip}{src if src.startswith('/') else '/' + src}"
        return None

    @property
    def media_image_remotely_accessible(self) -> bool:
        """If the image url is remotely accessible."""
        img_url = self.media_image_url
        if img_url and ("192.168." in img_url or "127.0.0.1" in img_url or "localhost" in img_url):
            return False
        return True

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        attrs = {}
        if self._current_item:
            attrs["item_id"] = self._current_item.get("id")
            attrs["item_title"] = self.media_title
            attrs["item_artist"] = self.media_artist
            attrs["item_type"] = self._current_item.get("type")
            attrs["item_year"] = self._current_item.get("year")
        if isinstance(self._gallery_status, dict):
            attrs["current_gallery"] = self._gallery_status.get("current_gallery")
            attrs["current_gallery_name"] = self._gallery_status.get("current_gallery_name")
        return attrs

    @property
    def shuffle(self):
        """Boolean if shuffling is enabled."""
        return self._meural_device.get("imageShuffle", False)

    async def async_set_device_option(
        self,
        orientation=None,
        orientationMatch=None,
        alsEnabled=None,
        alsSensitivity=None,
        goesDark=None,
        imageShuffle=None,
        imageDuration=None,
        previewDuration=None,
        overlayDuration=None,
        gestureFeedback=None,
        gestureFeedbackHelp=None,
        gestureFlip=None,
        backgroundColor=None,
        fillMode=None,
        schedulerEnabled=None,
        galleryRotation=None):
        """Set the configuration options on the Meural server."""
        params = {}
        if orientation is not None:
            params["orientation"] = orientation
        if orientationMatch is not None:
            params["orientationMatch"] = orientationMatch
        if alsEnabled is not None:
            params["alsEnabled"] = alsEnabled
        if alsSensitivity is not None:
            params["alsSensitivity"] = alsSensitivity
        if goesDark is not None:
            params["goesDark"] = goesDark
        if imageShuffle is not None:
            params["imageShuffle"] = imageShuffle
        if imageDuration is not None:
            params["imageDuration"] = imageDuration
        if previewDuration is not None:
            params["previewDuration"] = previewDuration
        if overlayDuration is not None:
            params["overlayDuration"] = overlayDuration
        if gestureFeedback is not None:
            params["gestureFeedback"] = gestureFeedback
        if gestureFeedbackHelp is not None:
            params["gestureFeedbackHelp"] = gestureFeedbackHelp
        if gestureFlip is not None:
            params["gestureFlip"] = gestureFlip
        if backgroundColor is not None:
            params["backgroundColor"] = backgroundColor
        if fillMode is not None:
            params["fillMode"] = fillMode
        if schedulerEnabled is not None:
            params["schedulerEnabled"] = schedulerEnabled
        if galleryRotation is not None:
            params["galleryRotation"] = galleryRotation
        _LOGGER.info("Meural device %s: Setting options. Setting options on Meural server", self.name)
        await self.meural.update_device(self.meural_device_id, params)

    async def async_set_brightness(self, brightness):
        """Change backlight brightness setting."""
        await self.local_meural.send_control_backlight(brightness)

    async def async_reset_brightness(self):
        """Automatically adjust backlight to room's lighting according to ambient light sensor."""
        await self.local_meural.send_als_calibrate_off()

    async def async_toggle_informationcard(self):
        """Toggle display of the information card."""
        await self.local_meural.send_key_up()

    async def async_synchronize(self):
        """Synchronize device with Meural server."""
        _LOGGER.info("Meural device %s: Synchronizing with Meural server", self.name)
        await self.meural.sync_device(self.meural_device_id)

    async def async_select_source(self, source):
        """Select playlist to display."""
        source = next((g["id"] for g in self._galleries if g["name"] == source), None)
        if source is None:
            _LOGGER.warning("Meural device %s: Selecting source. Source %s not found", self.name, source)
        await self.local_meural.send_change_gallery(source)

    async def async_media_previous_track(self):
        """Send previous image command."""
        if self._meural_device["gestureFlip"] == True:
            await self.local_meural.send_key_right()
        else:
            await self.local_meural.send_key_left()

    async def async_media_next_track(self):
        """Send next image command."""
        if self._meural_device["gestureFlip"] == True:
            await self.local_meural.send_key_left()
        else:
            await self.local_meural.send_key_right()

    async def async_turn_on(self):
        """Resume Meural frame display."""
        await self.local_meural.send_key_resume()

    async def async_turn_off(self):
        """Suspend Meural frame display."""
        await self.local_meural.send_key_suspend()

    async def async_media_pause(self):
        """Set duration to 0 (pause), store current duration in pause_duration."""
        self._pause_duration = self._meural_device["imageDuration"]
        _LOGGER.info("Meural device %s: Pausing player. Setting image duration on Meural server to 0", self.name)
        await self.meural.update_device(self.meural_device_id, {"imageDuration": 0})

    async def async_media_play(self):
        """Restore duration from pause_duration (play). Use duration 1800 if no pause_duration was stored."""
        if self._pause_duration != 0:
            _LOGGER.info("Meural device %s: Unpause player. Setting image duration on Meural server to %s", self.name, self._pause_duration)
            await self.meural.update_device(self.meural_device_id, {"imageDuration": self._pause_duration})
        else:
            _LOGGER.info("Meural device %s: Unpause player. Setting image duration on Meural server to 1800", self.name)
            await self.meural.update_device(self.meural_device_id, {"imageDuration": 1800})

    async def async_set_shuffle(self, shuffle):
        """Enable/disable shuffling."""
        _LOGGER.info("Meural device %s: Shuffling player. Setting shuffle on Meural server to %s", self.name, shuffle)
        await self.meural.update_device(self.meural_device_id, {"imageShuffle": shuffle})

    async def async_play_media(self, media_type, media_id, **kwargs):
        """Play media from media_source."""
        if media_source.is_media_source_id(media_id):
            sourced_media = await media_source.async_resolve_media(self.hass, media_id)
            media_type = sourced_media.mime_type
            media_id = sourced_media.url

            # If media ID is a relative URL, we serve it from HA.
            if media_id[0] == "/":
                user = await self.hass.auth.async_get_owner()
                if user.refresh_tokens:
                    refresh_token: RefreshToken = list(user.refresh_tokens.values())[0]

                    # Use kwargs so it works both before and after the change in Home Assistant 2022.2
                    media_id = async_sign_path(
                        hass=self.hass,
                        refresh_token_id=refresh_token.id,
                        path=media_id,
                        expiration=timedelta(minutes=5)
                    )

                # Prepend external URL.
                hass_url = get_url(self.hass, allow_internal=True)
                media_id = f"{hass_url}{media_id}"

            _LOGGER.info("Meural device %s: Playing media. Media type is %s, previewing image from %s", self.name, media_type, media_id)
            await self.local_meural.send_postcard(media_id, media_type)

        # Play gallery (playlist or album) by ID.
        elif media_type in ['playlist']:
            _LOGGER.info("Meural device %s: Playing media. Media type is %s, playing gallery %s", self.name, media_type, media_id)
            await self.local_meural.send_change_gallery(media_id)

        # "Preview image from URL.
        elif media_type in [ 'image/jpg', 'image/png', 'image/jpeg' ]:
            _LOGGER.info("Meural device %s: Playing media. Media type is %s, previewing image from %s", self.name, media_type, media_id)
            await self.local_meural.send_postcard(media_id, media_type)

        # Play item (artwork) by ID. Play locally if item is in currently displayed gallery. If not, play using Meural server."""
        elif media_type in ['item']:
            if media_id.isdigit():
                currentgallery_id = self._gallery_status["current_gallery"]
                currentitems = await self.local_meural.send_get_items_by_gallery(currentgallery_id)
                in_playlist = next((g["title"] for g in currentitems if g["id"] == media_id), None)
                if in_playlist is None:
                    _LOGGER.info("Meural device %s: Playing media. Item %s is not in current gallery, trying to display via Meural server", self.name, media_id)
                    try:
                        await self.meural.device_load_item(self.meural_device_id, media_id)
                    except:
                        _LOGGER.error("Meural device %s: Playing media. Error while trying to display %s item %s via Meural server", self.name, media_type, media_id, exc_info=True)
                else:
                    _LOGGER.info("Meural device %s: Playing media. Item %s is in current gallery %s, trying to display via local device", self.name, media_id, self._gallery_status["current_gallery_name"])
                    await self.local_meural.send_change_item(media_id)
            else:
                _LOGGER.error("Meural device %s: Playing media. ID %s is not an item", self.name, media_id)

        # This is an unsupported media type.
        else:
            _LOGGER.error("Meural device %s: Playing media. Does not support displaying this %s media with ID %s", self.name, media_type, media_id)

    async def async_preview_image(self, content_url, content_type):
        """Preview image from URL."""
        if content_type in [ 'image/jpg', 'image/png', 'image/jpeg' ]:
            _LOGGER.info("Meural device %s: Previewing image. Media type is %s, previewing image from %s", self.name, content_type, content_url)
            await self.local_meural.send_postcard(content_url, content_type)
        else:
            _LOGGER.error("Meural device %s: Previewing image. Does not support media type %s", self.name, content_type)

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""
        _LOGGER.debug("Meural device %s: Browsing media. Media_content_type is %s, media_content_id is %s", self.name, media_content_type, media_content_id)
        if media_content_id in (None, "") and media_content_type in (None, ""):
            response = BrowseMedia(
                title="Meural Canvas",
                media_class=MEDIA_CLASS_DIRECTORY,
                media_content_id="",
                media_content_type="",
                can_play=False,
                can_expand=True,
                children=[BrowseMedia(
                    title="Media Source",
                    media_class=MEDIA_CLASS_DIRECTORY,
                    media_content_id="",
                    media_content_type="localmediasource",
                    can_play=False,
                    can_expand=True),
                BrowseMedia(
                    title="Meural Playlists",
                    media_class=MEDIA_CLASS_DIRECTORY,
                    media_content_id="",
                    media_content_type="meuralplaylists",
                    can_play=False,
                    can_expand=True),
                ]
            )
            return response

        elif media_source.is_media_source_id(media_content_id) or media_content_type=="localmediasource":
            kwargs = {}
            if MAJOR_VERSION > 2022 or (MAJOR_VERSION == 2022 and MINOR_VERSION >= 2):
                kwargs['content_filter'] = lambda item: item.media_content_type in ('image/jpg', 'image/png', 'image/jpeg')

            response = await media_source.async_browse_media(self.hass, media_content_id, **kwargs)
            return response

        elif media_content_type=="meuralplaylists":
            response = BrowseMedia(
                title="Meural Playlists",
                media_class=MEDIA_CLASS_DIRECTORY,
                media_content_id="",
                media_content_type="",
                can_play=False,
                can_expand=True,
                children=[])

            device_galleries = await self.meural.get_device_galleries(self.meural_device_id)
            _LOGGER.info("Meural device %s: Browsing media. Getting %d device galleries from Meural server", self.name, len(device_galleries))
            user_galleries = await self.meural.get_user_galleries()
            _LOGGER.info("Meural device %s: Browsing media. Getting %d user galleries from Meural server", self.name, len(user_galleries))
            [device_galleries.append(x) for x in user_galleries if x not in device_galleries]
            self._remote_galleries = device_galleries
            _LOGGER.info("Meural device %s: Browsing media. Has %d unique remote galleries on Meural server" % (self.name, len(self._remote_galleries)))

            for g in self._galleries:

                thumb=next((h["cover"] for h in self._remote_galleries if h["id"] == int(g["id"])), None)
                if thumb == None and (int(g["id"])>4):
                    _LOGGER.debug("Meural device %s: Browsing media. Gallery %s misses thumbnail, getting gallery items", self.name, g["id"])
                    album_items = await self.local_meural.send_get_items_by_gallery(g["id"])
                    _LOGGER.info("Meural device %s: Browsing media. Replacing missing thumbnail of gallery %s with first gallery item image. Getting information from Meural server for item %s", self.name, g["id"], album_items[0]["id"])
                    first_item = await self.meural.get_item(album_items[0]["id"])
                    thumb = first_item["image"]
                _LOGGER.debug("Meural device %s: Browsing media. Thumbnail image for gallery %s is %s", self.name, g["id"], thumb)

                response.children.append(BrowseMedia(
                    title=g["name"],
                    media_class=MEDIA_TYPE_PLAYLIST,
                    media_content_id=g["id"],
                    media_content_type=MEDIA_TYPE_PLAYLIST,
                    can_play=True,
                    can_expand=False,
                    thumbnail=thumb,
                    )
                )
            return response

        else:
            _LOGGER.error("Meural device %s: Browsing media. Media not found, media_content_type is %s, media_content_id is %s", self.name, media_content_type, media_content_id)
            raise BrowseError(
                f"Media not found: {media_content_type} / {media_content_id}"
            )
