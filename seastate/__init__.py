def classFactory(iface):
    """Entry point required by QGIS to load the plugin."""
    from .seastate_plugin import SeaStatePlugin
    return SeaStatePlugin(iface)
