import configparser
import sys
from pathlib import Path

from qgis.core import Qgis, QgsApplication, QgsProject
from qgis.PyQt.QtCore import QFile
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMessageBox, QStyle, QWidget
from qgis.utils import iface

from . import resources_rc  # noqa
from .layer_connector import LayerConnector
from .reader import Reader
from .settings import Settings
from .toolbox import log
from .writer import Writer


class MemoryLayerSaver(LayerConnector):
    def __init__(self):
        super().__init__()
        proj = QgsProject.instance()
        self.has_modified_layers = proj.isDirty()

        proj.readProject.connect(self.load_data)
        proj.writeProject.connect(self.save_data)
        proj.cleared.connect(self.on_cleared)

    def initGui(self):  # noqa
        self.menu = iface.pluginMenu().addMenu(
            QIcon(":/plugins/memory_layer_saver/icon.svg"), self.tr("Memory layer saver")
        )
        self.menu.setObjectName("memory_layer_saver_menu")

        self.about_action = self.menu.addAction(
            self.menu.style().standardIcon(QStyle.SP_MessageBoxInformation), self.tr("About")
        )
        self.about_action.setObjectName("memory_layer_saver_about")
        self.about_action.triggered.connect(self.show_about)

        self.info_action = self.menu.addAction(
            QIcon(":/plugins/memory_layer_saver/icon.svg"), self.tr("Display memory layer information")
        )
        self.info_action.setObjectName("memory_layer_saver_info")
        self.info_action.triggered.connect(self.show_info)

        # Disable the prompt to save memory layers on exit since we are saving them automatically
        Settings.set_ask_to_save_memory_layers(False)
        log("MemoryLayerSaver loaded")

    def tr(self, message, *args, **kwargs):
        """Get the translation for a string using Qt translation API."""
        return QgsApplication.translate("MemoryLayerSaver", message, *args, **kwargs)

    def unload(self):
        iface.pluginMenu().removeAction(self.menu.menuAction())
        self.detach()
        proj = QgsProject.instance()
        proj.readProject.disconnect(self.load_data)
        proj.writeProject.disconnect(self.save_data)

        # Restore the original value of the setting
        Settings.set_ask_to_save_memory_layers(Settings.backup_ask_to_save_memory_layers())
        log("MemoryLayerSaver unloaded")

    def on_cleared(self):
        self.has_modified_layers = False

    def connect_layer(self, layer):
        if Settings.is_saved_layer(layer):
            layer.committedAttributesDeleted.connect(self.set_project_dirty)
            layer.committedAttributesAdded.connect(self.set_project_dirty)
            layer.committedFeaturesRemoved.connect(self.set_project_dirty)
            layer.committedFeaturesAdded.connect(self.set_project_dirty)
            layer.committedAttributeValuesChanges.connect(self.set_project_dirty)
            layer.committedGeometriesChanges.connect(self.set_project_dirty)
            # Connect layer will be called when a layer is added to the project
            # So we set the has_modified_layers flag to ensure the mldata file will be
            # updated when the project is saved
            self.has_modified_layers = True

    def disconnect_layer(self, layer):
        if Settings.is_saved_layer(layer):
            layer.committedAttributesDeleted.disconnect(self.set_project_dirty)
            layer.committedAttributesAdded.disconnect(self.set_project_dirty)
            layer.committedFeaturesRemoved.disconnect(self.set_project_dirty)
            layer.committedFeaturesAdded.disconnect(self.set_project_dirty)
            layer.committedAttributeValuesChanges.disconnect(self.set_project_dirty)
            layer.committedGeometriesChanges.disconnect(self.set_project_dirty)
            # Disconnect layer will be called when a layer is removed from the project
            # So we set the has_modified_layers flag to ensure the mldata file will be
            # updated when the project is saved
            self.has_modified_layers = True

    def load_data(self):
        filename = self.memory_layer_file()
        file = QFile(filename)
        if file.exists():
            log("Loading memory layers from " + filename)
            layers = list(self.memory_layers())
            if layers:
                try:
                    with Reader(filename) as reader:
                        reader.read_layers(layers)
                except BaseException:
                    QMessageBox.information(
                        iface.mainWindow(), self.tr("Error reloading memory layers"), str(sys.exc_info()[1])
                    )

        self.has_modified_layers = False

    def save_data(self):
        if not self.has_modified_layers:
            return

        if Qgis.versionInt() >= 32200:
            QgsProject.instance().createAttachedFile("layers.mldata")

        filename = self.memory_layer_file()
        log("Saving memory layers to " + filename)
        layers = list(self.memory_layers())
        if layers:
            with Writer(filename) as writer:
                writer.write_layers(layers)

        self.has_modified_layers = False

    def memory_layers(self):
        return [layer for layer in QgsProject.instance().mapLayers().values() if Settings.is_saved_layer(layer)]

    def memory_layer_file(self):
        name = QgsProject.instance().fileName()
        if not name:
            return ""

        # Check if the mldata was embedded in the project file
        if Qgis.versionInt() >= 32200:
            for attachment in QgsProject.instance().attachedFiles():
                if attachment.endswith("layers.mldata"):
                    return attachment

        return name + ".mldata"

    def set_project_dirty(self):
        self.has_modified_layers = True
        QgsProject.instance().setDirty(True)

    def show_info(self):
        layer_info = [(layer.name(), layer.featureCount()) for layer in self.memory_layers()]
        if layer_info:
            message = self.tr("The following memory layers will be saved with this project:")
            message += "<br>"
            message += "<br>".join(
                self.tr("- <b>{0}</b> ({1} features)", "Layer name and number of features", n=count).format(name, count)
                for name, count in layer_info
            )
        else:
            message = self.tr("This project contains no memory layers to be saved")
        QMessageBox.information(iface.mainWindow(), "Memory Layer Saver", message)

    def show_about(self):
        # Used to display plugin icon in the about message box
        bogus = QWidget(iface.mainWindow())
        bogus.setWindowIcon(QIcon(":/plugins/memory_layer_saver/icon.svg"))

        # Get plugin metadata
        cfg = configparser.ConfigParser()
        cfg.read(Path(__file__).parent / "metadata.txt")

        name = cfg.get("general", "name")
        version = cfg.get("general", "version")
        repository = cfg.get("general", "repository")
        tracker = cfg.get("general", "tracker")
        homepage = cfg.get("general", "homepage")
        QMessageBox.about(
            bogus,
            self.tr("About {0}").format(name),
            "<b>Version</b> {}<br><br>"
            "<b>{}</b> : <a href={}>GitHub</a><br>"
            "<b>{}</b> : <a href={}/issues>GitHub</a><br>"
            "<b>{}</b> : <a href={}>GitHub</a>".format(
                version,
                self.tr("Source code"),
                repository,
                self.tr("Report issues"),
                tracker,
                self.tr("Documentation"),
                homepage,
            ),
        )
        bogus.deleteLater()
