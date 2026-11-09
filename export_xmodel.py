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

import bpy
import bmesh
from mathutils import Matrix

from . import shared as shared
from .PyCoD import xmodel as XModel


def _skip_notice(ob_name, mesh_name, notice):
    vargs = (ob_name, mesh_name, notice)
    print("\nSkipped object \"%s\" (mesh \"%s\"): %s" % vargs)


def mesh_triangulate(mesh, vertex_cleanup):
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=bm.faces)
        if vertex_cleanup:
            bmesh.ops.split(bm, use_only_faces=True)
        bm.to_mesh(mesh)
    finally:
        bm.free()


def gather_exportable_objects(context, use_selection, use_armature):
    active = context.active_object
    candidates = context.selected_objects if use_selection else context.view_layer.objects
    meshes = [obj for obj in candidates if obj.type == 'MESH']

    armature = None
    if use_armature:
        if active is not None:
            armature = active if active.type == 'ARMATURE' else active.find_armature()
        if armature is None:
            armature = next((mesh.find_armature() for mesh in meshes
                             if mesh.find_armature() is not None), None)
        if armature is not None and not armature.data.bones:
            armature = None
    if armature is not None:
        meshes = [mesh for mesh in meshes if mesh.find_armature() == armature]

    return armature, meshes


def material_gen_image_dict(material):
    '''
    Generate a PyCoD compatible image dict from a given Blender material
    '''
    images = {}
    if not material or not material.use_nodes or not material.node_tree:
        return images
    shader = next((node for node in material.node_tree.nodes
                   if node.type == 'BSDF_PRINCIPLED'), None)
    if shader is None:
        return images
    for key, socket_name in (('color', 'Base Color'), ('normal', 'Normal')):
        links = shader.inputs[socket_name].links
        if not links:
            continue
        node = links[0].from_node
        if key == 'normal' and node.type == 'NORMAL_MAP':
            links = node.inputs['Color'].links
            if not links:
                continue
            node = links[0].from_node
        if node.type == 'TEX_IMAGE' and node.image:
            images[key] = (bpy.path.basename(node.image.filepath)
                           if node.image.filepath else node.image.name)
    return images


class ExportMesh(object):
    '''
    Internal class used for handling the conversion of mesh data into
    a PyCoD compatible format
    '''
    __slots__ = ('mesh', 'object', 'matrix', 'weights', 'materials')

    def __init__(self, obj, mesh, model_materials, matrix):
        self.mesh = mesh
        self.object = obj
        self.matrix = matrix
        self.weights = [None] * len(mesh.vertices)

        # Used to map mesh materials indices to our model material indices
        self.materials = []
        self.gen_material_indices(model_materials)

    def add_weights(self, bone_table, weight_min_threshold=0.0):
        bone_indices = {name: index for index, name in enumerate(bone_table)}
        group_map = [bone_indices.get(group.name) for group in self.object.vertex_groups]
        for vert in self.mesh.vertices:
            weights = []
            for group in vert.groups:
                if group.group >= len(group_map):
                    continue
                bone_index = group_map[group.group]
                if (bone_index is not None and group.weight > 0
                        and group.weight >= weight_min_threshold):
                    weights.append((bone_index, group.weight))
            if weights and weight_min_threshold > 0:
                total = sum(weight for _, weight in weights)
                weights = [(bone, weight / total) for bone, weight in weights]
            self.weights[vert.index] = weights or [(0, 1.0)]

    def gen_material_indices(self, model_materials):
        slots = list(self.mesh.materials) or [None]
        self.materials = [None] * len(slots)
        for material_index, material in enumerate(slots):
            if material in model_materials:
                self.materials[material_index] = model_materials.index(material)  # nopep8
            else:
                self.materials[material_index] = len(model_materials)
                model_materials.append(material)

    def to_xmodel_mesh(self, mesh_id, use_vertex_colors, use_alpha,
                       use_alpha_mode, global_scale):
        mesh = XModel.Mesh(self.mesh.name)
        uv_layer = self.mesh.uv_layers.active
        attributes = getattr(self.mesh, 'color_attributes', None)
        if attributes is not None:
            color_layer = attributes.active_color if use_vertex_colors else None
        else:
            attributes = self.mesh.vertex_colors
            color_layer = attributes.active if use_vertex_colors else None
        alpha_layer = color_layer
        if color_layer is not None and use_alpha_mode == 'SECONDARY':
            alpha_layer = next((layer for layer in attributes
                                if layer.name != color_layer.name), color_layer)

        def sample_color(layer, loop_index, vertex_index):
            index = vertex_index if getattr(layer, 'domain', 'CORNER') == 'POINT' else loop_index
            value = layer.data[index]
            return getattr(value, 'color_srgb', value.color)

        basis = self.matrix.to_3x3()
        normal_matrix = basis.inverted_safe().transposed()
        loop_order = (0, 1, 2) if basis.determinant() < 0 else (0, 2, 1)
        for vert in self.mesh.vertices:
            offset = (self.matrix @ vert.co) * global_scale
            mesh.verts.append(XModel.Vertex(tuple(offset), self.weights[vert.index]))

        for polygon in self.mesh.polygons:
            material_id = self.materials[polygon.material_index]
            face = XModel.Face(mesh_id, material_id)
            for index, corner in enumerate(loop_order):
                loop_index = polygon.loop_indices[corner]
                loop = self.mesh.loops[loop_index]
                uv = uv_layer.data[loop_index].uv if uv_layer else (0.0, 0.0)
                color = (1.0, 1.0, 1.0, 1.0)
                if color_layer is not None:
                    rgb = sample_color(color_layer, loop_index, loop.vertex_index)[:3]
                    alpha = 1.0
                    if use_alpha:
                        alpha_rgb = sample_color(alpha_layer, loop_index, loop.vertex_index)[:3]
                        alpha = sum(alpha_rgb) / 3.0
                    color = tuple(max(0.0, min(1.0, value)) for value in (*rgb, alpha))
                face.indices[index] = XModel.FaceVertex(
                    loop.vertex_index, (normal_matrix @ loop.normal).normalized(),
                    color, (uv[0], 1.0 - uv[1]))
            mesh.faces.append(face)
        return mesh


def save(self, context, filepath,
         target_format='XMODEL_EXPORT',
         version='6',
         use_selection=False,
         global_scale=1.0,
         apply_unit_scale=False,
         apply_modifiers=True,
         modifier_quality='PREVIEW',
         use_vertex_colors=False,
         use_vertex_colors_alpha=False,
         use_vertex_colors_alpha_mode='SECONDARY',
         use_vertex_cleanup=False,
         use_armature=True,
         use_weight_min=False,
         use_weight_min_threshold=0.010097,
         ):

    if apply_unit_scale:
        global_scale *= shared.calculate_unit_scale_factor(context.scene)

    active = context.active_object
    last_mode = active.mode if active is not None else 'OBJECT'
    if last_mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    try:
        armature, objects = gather_exportable_objects(context, use_selection, use_armature)
        if not objects:
            return "There are no meshes to export."
        return save_model(
            self, context, filepath, armature, objects, target_format, int(version),
            global_scale, apply_modifiers, modifier_quality, use_vertex_colors,
            use_vertex_colors_alpha, use_vertex_colors_alpha_mode, use_vertex_cleanup,
            use_armature, use_weight_min, use_weight_min_threshold)
    finally:
        if last_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=last_mode)


def save_model(self, context, filepath, armature, objects,
               target_format,
               version,
               global_scale,
               apply_modifiers,
               modifier_quality,
               use_vertex_colors,
               use_vertex_colors_alpha,
               use_vertex_colors_alpha_mode,
               use_vertex_cleanup,
               use_armature,
               use_weight_min,
               use_weight_min_threshold,
               ):
    model = XModel.Model("$export")
    materials = []
    export_from_world = Matrix.Identity(4)

    # Build the bone hierarchy & transform matrices
    if use_armature and armature is not None:
        # CoD writes the first bone at the origin with identity rotation.
        root = armature.data.bones[0]
        export_from_world = (armature.matrix_world @ root.matrix_local).inverted_safe()
        armature_matrix = export_from_world @ armature.matrix_world
        bone_table = [b.name for b in armature.data.bones]
        bone_indices = {name: index for index, name in enumerate(bone_table)}
        for bone_index, bone in enumerate(armature.data.bones):
            parent_index = bone_indices[bone.parent.name] if bone.parent else -1
            model_bone = XModel.Bone(bone.name, parent_index)

            if bone_index == 0:
                matrix = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
                offset = (0, 0, 0)
            else:
                mtx = (armature_matrix @
                       bone.matrix_local).to_quaternion().to_matrix().transposed()
                matrix = [tuple(mtx[0]), tuple(mtx[1]), tuple(mtx[2])]
                offset = (armature_matrix @ bone.head_local) * global_scale

            model_bone.offset = tuple(offset)
            model_bone.matrix = matrix
            model.bones.append(model_bone)
    else:
        # If there are no bones, or there is no armature
        #  create a dummy bone for tag_pos
        dummy_bone_name = "tag_origin"
        dummy_bone = XModel.Bone(dummy_bone_name, -1)
        dummy_bone.offset = (0, 0, 0)
        dummy_bone.matrix = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
        model.bones.append(dummy_bone)
        bone_table = []

    # Generate bone weights for verts
    if not use_weight_min:
        use_weight_min_threshold = 0.0
    for ob in objects:
        modifier_states = [(mod, mod.show_viewport) for mod in ob.modifiers]
        subdivision_states = []
        try:
            for mod, enabled in modifier_states:
                mod.show_viewport = enabled and apply_modifiers and mod.type != 'ARMATURE'
                if mod.show_viewport and modifier_quality == 'RENDER' and mod.type in {'SUBSURF', 'MULTIRES'}:
                    subdivision_states.append((mod, mod.levels))
                    mod.levels = mod.render_levels

            context.view_layer.update()
            depsgraph = context.evaluated_depsgraph_get()
            evaluated = ob.evaluated_get(depsgraph)
            try:
                mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
                if mesh is None or not mesh.polygons:
                    _skip_notice(ob.name, ob.data.name, "No faces")
                    continue
                mesh_triangulate(mesh, use_vertex_cleanup)
                if bpy.app.version < (4, 1, 0):
                    mesh.calc_normals_split()
                if mesh.uv_layers.active is None:
                    self.report({'WARNING'}, "%s has no UV map. Exported default UV coordinates." % ob.name)
                export_mesh = ExportMesh(evaluated, mesh, materials,
                                         export_from_world @ evaluated.matrix_world)
                export_mesh.add_weights(bone_table, use_weight_min_threshold)
                model.meshes.append(export_mesh.to_xmodel_mesh(
                    len(model.meshes), use_vertex_colors, use_vertex_colors_alpha,
                    use_vertex_colors_alpha_mode, global_scale))
            finally:
                evaluated.to_mesh_clear()
        finally:
            for mod, levels in subdivision_states:
                mod.levels = levels
            for mod, enabled in modifier_states:
                mod.show_viewport = enabled

    if not model.meshes:
        return "There are no mesh faces to export."

    missing_count = 0
    for material in materials:
        imgs = material_gen_image_dict(material)
        if material is None:
            name = "material" + str(missing_count)
            missing_count += 1
        else:
            name = material.name

        if version == 5 and target_format == 'XMODEL_EXPORT' and 'color' not in imgs:
            imgs['color'] = next(iter(imgs.values()), "")
        mtl = XModel.Material(name, "Lambert", imgs)
        model.materials.append(mtl)

    header_msg = shared.get_metadata_string(filepath)
    if target_format == 'XMODEL_BIN':
        model.WriteFile_Bin(filepath, version=version,
                            header_message=header_msg)
    else:
        model.WriteFile_Raw(filepath, version=version, header_message=header_msg,
                            extended_features=version != 5)
