# dmgbuild settings. Invoked as:
#   PORTABLEAI_APP=/path/to/PortableAI.app dmgbuild -s dmg_settings.py PortableAI out.dmg
import os

application = os.environ["PORTABLEAI_APP"]
files = [application]
symlinks = {"Applications": "/Applications"}
icon_locations = {
    os.path.basename(application): (140, 160),
    "Applications": (400, 160),
}
window_rect = ((200, 160), (560, 400))
format = "UDZO"
