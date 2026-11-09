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

import os
import bpy
import bmesh
from mathutils import *
from struct import error as StructError
from bpy_extras.image_utils import load_image

from . import shared as shared
from .PyCoD import xmodel as XModel


def get_armature_for_object(ob):
    '''
    Get the armature for a given object.
    If the object *is* an armature, the object itself is returned.
    '''
    if ob is None:
        return None

    if ob.type == 'ARMATURE':
        return ob

    return ob.find_armature()


def get_armature_modifier_for_object(ob):
    for mod in ob.modifiers:
        if mod.type == 'ARMATURE':
            return mod
    return None


def join_armatures(context, target, source, meshes):
    context.view_layer.update()
    mesh_matrices = [mesh.matrix_world.copy() for mesh in meshes]
    transform = target.matrix_world.inverted_safe() @ source.matrix_world
    context.view_layer.objects.active = target
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        edit_bones = target.data.edit_bones
        new_bones = {}
        for bone in source.data.bones:
            if bone.name in edit_bones:
                continue
            edit_bone = edit_bones.new(bone.name)
            edit_bone.head = transform @ bone.head_local
            edit_bone.tail = transform @ bone.tail_local
            edit_bone.align_roll(transform.to_3x3() @ bone.matrix_local.col[2].xyz)
            edit_bone.use_local_location = bone.use_local_location
            new_bones[bone.name] = edit_bone
        for bone in source.data.bones:
            if bone.name in new_bones and bone.parent:
                new_bones[bone.name].parent = edit_bones[bone.parent.name]
        for child, parent in (('j_gun', 'tag_weapon'), ('tag_weapon', 'tag_weapon_right')):
            if child in new_bones and parent in edit_bones:
                edit_bones[child].parent = edit_bones[parent]
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')

    for mesh, matrix in zip(meshes, mesh_matrices):
        modifier = get_armature_modifier_for_object(mesh)
        modifier.object = target
        mesh.parent = target
        mesh.matrix_world = matrix
    source_data = source.data
    bpy.data.objects.remove(source, do_unlink=True)
    if not source_data.users:
        bpy.data.armatures.remove(source_data)


def setup_material(material, images, search_dir, recursive):
    material.use_nodes = True
    tree = material.node_tree
    shader = next(node for node in tree.nodes if node.type == 'BSDF_PRINCIPLED')
    for image_type, input_name in (('color', 'Base Color'), ('normal', 'Normal')):
        image_name = images.get(image_type)
        if not image_name or image_name == '$none.tga':
            continue
        image = load_image(image_name, dirname=search_dir, recursive=recursive,
                           check_existing=True)
        if image is None:
            continue
        texture = tree.nodes.new('ShaderNodeTexImage')
        texture.image = image
        texture.location = (-520, 240 if image_type == 'color' else -40)
        if image_type == 'normal':
            if image.colorspace_settings.name != 'Non-Color':
                image = image.copy()
                image.colorspace_settings.name = 'Non-Color'
                texture.image = image
            normal = tree.nodes.new('ShaderNodeNormalMap')
            normal.location = (-240, -40)
            tree.links.new(texture.outputs['Color'], normal.inputs['Color'])
            tree.links.new(normal.outputs['Normal'], shader.inputs[input_name])
        else:
            tree.links.new(texture.outputs['Color'], shader.inputs[input_name])


def load(self, context,
         filepath,
         global_scale=1.0,
         apply_unit_scale=False,
         use_single_mesh=True,
         use_dup_tris=True,
         use_custom_normals=True,
         use_vertex_colors=True,
         use_armature=True,
         use_parents=True,
         attach_model=False,
         merge_skeleton=False,
         use_image_search=True):

    # Apply unit conversion factor to the scale
    if apply_unit_scale:
        global_scale /= shared.calculate_unit_scale_factor(context.scene)

    target_scale = global_scale

    if use_armature is False:
        attach_model = False

    skel_old = get_armature_for_object(context.active_object)
    if skel_old is None:
        attach_model = False

    if not attach_model:
        merge_skeleton = False

    split_meshes = not use_single_mesh

    scene = bpy.context.scene
    view_layer = bpy.context.view_layer

    # Load the model
    model_name = os.path.basename(filepath)
    model = XModel.Model(('.').join(model_name.split('.')[:-1]))

    ext = os.path.splitext(filepath)[-1].upper()
    if ext == '.XMODEL_BIN':
        LoadModelFile = model.LoadFile_Bin
    else:
        LoadModelFile = model.LoadFile_Raw

    try:
        LoadModelFile(filepath, split_meshes=split_meshes)
    except (OSError, ValueError, AttributeError, TypeError, IndexError, StructError) as error:
        return "Invalid XMODEL '%s': %s" % (model_name, error)

    if not model.meshes or not any(mesh and mesh.verts for mesh in model.meshes):
        return "XMODEL has no mesh vertices."
    if any(mesh is None for mesh in model.meshes) or any(mat is None for mat in model.materials):
        return "XMODEL contains incomplete objects or materials."
    for mesh in model.meshes:
        if any(vert.offset is None for vert in mesh.verts):
            return "XMODEL contains incomplete vertices."
        if any(face.material_id is None or not 0 <= face.material_id < len(model.materials)
               or any(index is None or not 0 <= index.vertex < len(mesh.verts)
                      for index in face.indices) for face in mesh.faces):
            return "XMODEL contains incomplete or invalid faces."
    if use_armature and not model.bones:
        use_armature = attach_model = merge_skeleton = False
    if use_armature and any(not bone.name or bone.offset is None
                            or any(axis is None for axis in bone.matrix)
                            for bone in model.bones):
        return "XMODEL contains incomplete bones."

    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    materials = []
    for material in model.materials:
        mat = bpy.data.materials.get(material.name)
        if mat is None:
            mat = bpy.data.materials.new(name=material.name)
            setup_material(mat, material.images, os.path.dirname(filepath), use_image_search)
        materials.append(mat)

    # Meshes
    mesh_objs = []  # Mesh objects that we're going to link the skeleton to
    for sub_mesh in model.meshes:
        if split_meshes is False:
            sub_mesh.name = "%s_mesh" % model.name
        print("Creating mesh: '%s'" % sub_mesh.name)
        mesh = bpy.data.meshes.new(sub_mesh.name)
        bm = bmesh.new()

        # Add UV Layers
        uv_layer = bm.loops.layers.uv.new("UVMap")

        # Add Vertex Color Layer
        if use_vertex_colors:
            vert_color_layer = bm.loops.layers.color.new("Color")

        # Add Verts
        for vert in sub_mesh.verts:
            bm.verts.new(Vector(vert.offset) * target_scale)
        bm.verts.ensure_lookup_table()

        # Contains data for faces that use the same 3 verts as an existing face
        #  (usually caused by `double sided` tris)
        dup_faces = []
        dup_verts = []

        # Contains vertex mapping data for all verts that the dup faces use
        dup_verts_mapping = {}
        duplicate_counts = {}


        loop_normals = []  # List of normals for every added loop (face vertex)

        # Tracks how many faces in the current mesh use a given material
        material_usage_counts = [0] * len(materials)

        # Inner function used set up a bmesh tri's normals (into loop_normals),
        #  uv, materials, etc.
        def setup_tri(f):
            # Assign the face's material & increment the material usage counter
            material_index = face.material_id
            f.material_index = material_index

            material_usage_counts[material_index] += 1

            # Assign the face's UV layer
            for loop_index, loop in enumerate(f.loops):
                face_index_loop = face.indices[loop_index]
                # Normal
                loop_normals.append(face_index_loop.normal)
                # UV Coordinate Correction
                uv = Vector(face_index_loop.uv)
                uv.y = 1.0 - uv.y
                loop[uv_layer].uv = uv
                # Vertex Colors
                if use_vertex_colors:
                    loop[vert_color_layer] = face_index_loop.color or (1.0, 1.0, 1.0, 1.0)

        unused_faces = []

        vert_count = len(sub_mesh.verts)
        for face_index, face in enumerate(sub_mesh.faces):
            # Fix the winding order
            tmp = face.indices[2]
            face.indices[2] = face.indices[1]
            face.indices[1] = tmp

            indices = [bm.verts[index.vertex] for index in face.indices]

            try:
                f = bm.faces.new(indices)
            except ValueError:
                # Mark the face as unused
                unused_faces.append(face)

                if not face.isValid():
                    print("TRI %d is invalid! %s" %
                          (face_index, [index.vertex for index in face.indices]))
                    continue

                if not use_dup_tris:
                    continue
                key = tuple(sorted(index.vertex for index in face.indices))
                depth = duplicate_counts.get(key, 0) + 1
                duplicate_counts[key] = depth
                for index in face.indices:
                    vert = index.vertex
                    mapping_key = (depth, vert)
                    if mapping_key not in dup_verts_mapping:
                        dup_verts_mapping[mapping_key] = len(dup_verts) + vert_count
                        dup_verts.append(sub_mesh.verts[vert])
                    index.vertex = dup_verts_mapping[mapping_key]
                dup_faces.append(face)
            else:
                setup_tri(f)

        # Remove the unused tris so they aren't accidentally used later
        for face in unused_faces:
            sub_mesh.faces.remove(face)

        if use_dup_tris:
            for vert in dup_verts:
                bm.verts.new(Vector(vert.offset) * target_scale)
            bm.verts.ensure_lookup_table()

            for face in dup_faces:
                indices = [bm.verts[index.vertex] for index in face.indices]
                try:
                    f = bm.faces.new(indices)
                except ValueError:
                    pass  # Skip dups of dups
                else:
                    setup_tri(f)

        # Vertex Weights
        deform_layer = bm.verts.layers.deform.new()
        for vert_index, vert in enumerate(sub_mesh.verts):
            for bone, weight in vert.weights:
                bm.verts[vert_index][deform_layer][bone] = weight

        if use_dup_tris:
            offset = len(sub_mesh.verts)
            for vert_index, vert in enumerate(dup_verts):
                for bone, weight in vert.weights:
                    bm.verts[vert_index + offset][deform_layer][bone] = weight

        # Assign Materials
        for mat in materials:
            mesh.materials.append(mat)

        bm.to_mesh(mesh)

        # For this mesh remove all materials that aren't used by its faces
        # material_index, material_usage_index must be tracked manually because
        # enumerate() doesn't compensate for the removed materials properly
        material_index = 0
        material_usage_index = 0
        for material in mesh.materials:
            if material_usage_counts[material_usage_index] == 0:
                # Note: update_data must be True, otherwise - after the first
                #  material is removed, the indices are invalidated
                mesh.materials.pop(index=material_index)
            else:
                material_index += 1
            material_usage_index += 1

        if split_meshes:
            obj_name = "%s_%s" % (model.name, mesh.name)
        else:
            obj_name = model.name

        obj = bpy.data.objects.new(obj_name, mesh)
        # Mesh validation discards deform weights without matching groups.
        for bone in model.bones:
            obj.vertex_groups.new(name=bone.name.lower())

        # Custom Normals
        if use_custom_normals:
            # Store 'temp' normals in loops, since validate() may alter the
            #  final mesh.
            # We can only set custom loop normals *after* calling it.
            if bpy.app.version < (4, 1, 0):
                mesh.create_normals_split()

            # Iterate over every single loop (every vert for every face)
            # for loop_index, loop in enumerate(mesh.loops):
            #     mesh.loops[loop_index].normal = loop_normals[loop_index]

            # mesh.free_normals_split() # Is this necessary?

            # mesh.loops.foreach_get("normal", clnors)

            # Enable Smoothing - must be BEFORE normals_split_custom_set, etc.
            polygon_count = len(mesh.polygons)
            mesh.polygons.foreach_set("use_smooth", [True] * polygon_count)

            mesh.normals_split_custom_set(loop_normals)
            if bpy.app.version < (4, 1, 0):
                mesh.use_auto_smooth = True

            # *Very* important to not remove loop normals here!
            mesh.validate(clean_customdata=False)

            # This was used to highlight sharp edges in legacy versions
            # In Blender 2.8x, it uses the View3D Overlay API - but since
            # it's enabled by default in those versions, we don't need any
            # special code
            # mesh.show_edge_sharp = True

        else:
            mesh.validate()

            # Enable Smoothing
            polygon_count = len(mesh.polygons)
            mesh.polygons.foreach_set("use_smooth", [True] * polygon_count)

            # Use Auto-generated Normals
            if bpy.app.version < (4, 1, 0):
                mesh.calc_normals()

        # Link the validated mesh object to the scene
        mesh_objs.append(obj)

        scene.collection.objects.link(obj)
        view_layer.objects.active = obj

    if use_armature:
        # Create the skeleton
        armature = bpy.data.armatures.new("%s_amt" % model.name)
        armature.display_type = "STICK"

        skel_obj = bpy.data.objects.new("%s_skel" % model.name, armature)
        skel_obj.show_in_front = True

        # Add the skeleton object to the scene
        scene.collection.objects.link(skel_obj)
        view_layer.objects.active = skel_obj

        bpy.ops.object.mode_set(mode='EDIT')

        edit_bones = []
        for bone in model.bones:
            edit_bone = armature.edit_bones.new(bone.name.lower())
            edit_bone.use_local_location = False

            offset = Vector(bone.offset) * target_scale
            axis = Vector(bone.matrix[1]) * target_scale
            roll = Vector(bone.matrix[2])

            edit_bone.head = offset
            edit_bone.tail = offset + axis
            edit_bone.align_roll(roll)

            edit_bones.append(edit_bone)

        if use_parents:
            for bone, edit_bone in zip(model.bones, edit_bones):
                if 0 <= bone.parent < len(edit_bones):
                    edit_bone.parent = edit_bones[bone.parent]

        # HACK: Force the pose bone list for the armature to be rebuilt
        bpy.ops.object.mode_set(mode='OBJECT')

        # Add the armature modifier to each mesh object
        for mesh_obj in mesh_objs:
            mesh_obj.parent = skel_obj
            modifier = mesh_obj.modifiers.new('Armature', 'ARMATURE')
            modifier.object = skel_obj
            modifier.use_bone_envelopes = False
            modifier.use_vertex_groups = True

        if attach_model:
            root_name = skel_obj.data.bones[0].name
            target_name = {'j_gun': 'tag_weapon', 'tag_weapon': 'tag_weapon_right'}.get(
                root_name, root_name)
            if target_name in skel_old.data.bones:
                skel_obj.parent = skel_old
                skel_obj.parent_type = 'BONE'
                skel_obj.parent_bone = target_name
                skel_obj.location = (0, -1, 0)
            elif not merge_skeleton:
                self.report({'WARNING'}, "Attachment bone '%s' was not found. Imported separately." % target_name)

            if merge_skeleton:
                join_armatures(context, skel_old, skel_obj, mesh_objs)
