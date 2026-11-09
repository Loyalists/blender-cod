# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

# <pep8 compliant>

plugin_preferences = None


def get_metadata_string(filepath):
    import bpy
    msg = "// Exported using Blender v%s\n" % bpy.app.version_string
    msg += "// Export filename: '%s'\n" % filepath.replace("\\", "/")
    if bpy.data.filepath is None:
        source_file = "<none>"
    else:
        source_file = bpy.data.filepath.replace('\\', '/')
    msg += "// Source filename: '%s'\n" % source_file
    return msg


def calculate_unit_scale_factor(scene):
    """Return the number of CoD inches per Blender unit."""
    if scene.unit_settings.system != 'NONE':
        return scene.unit_settings.scale_length / 0.0254
    if plugin_preferences is not None:
        return plugin_preferences.scale_length / 0.0254
    return 1.0
