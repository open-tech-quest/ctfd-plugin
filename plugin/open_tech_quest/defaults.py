"""Fresh room objects for installation and explicit default-room restoration."""
from .models import QuestRoom

DEFAULT_ROOMS = (('openshift', 'OpenShift'), ('ansible', 'Ansible'), ('rhel', 'RHEL'),
                 ('security', 'Security'), ('ai', 'AI'))


def default_rooms():
    return [QuestRoom(slug=slug, name=name, position=position,
                      theme={'accent': '#00a6b2', 'layout': slug,
                             'logo_url': None, 'hero_url': None})
            for position, (slug, name) in enumerate(DEFAULT_ROOMS)]
