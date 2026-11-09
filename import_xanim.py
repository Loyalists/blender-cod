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
from math import isfinite
from struct import error as StructError
from mathutils import Matrix, Vector

from . import shared
from .PyCoD import xanim as XAnim


def load(self, context, apply_unit_scale=False, **keywords):
    active = context.active_object
    armature = active if active and active.type == 'ARMATURE' else (
        active.find_armature() if active else None)
    if armature is None:
        return "No active armature found."

    filepath = keywords.pop('filepath')
    directory = os.path.dirname(filepath)
    paths = [os.path.join(directory, item.name) for item in self.files] or [filepath]
    if apply_unit_scale:
        keywords['global_scale'] /= shared.calculate_unit_scale_factor(context.scene)
    if not keywords['use_notetracks']:
        keywords['use_notetrack_file'] = False

    imported = 0
    last_error = None
    for path in paths:
        try:
            anim = XAnim.Anim()
            extension = os.path.splitext(path)[1].upper()
            if extension == '.XANIM_BIN':
                anim.LoadFile_Bin(path)
            elif extension == '.XANIM_EXPORT':
                anim.LoadFile_Raw(path, keywords['use_notetrack_file'])
            else:
                raise ValueError("Select an XANIM_EXPORT or XANIM_BIN file.")
            if (getattr(anim, 'version', None) != 3 or not anim.parts or not anim.frames
                    or not isfinite(anim.framerate) or anim.framerate <= 0):
                raise ValueError("Invalid or empty XANIM animation.")
            if any(part is None or not part.name for part in anim.parts):
                raise ValueError("Incomplete animation part list.")
            if any(frame is None or not isfinite(frame.frame)
                   or len(frame.parts) != len(anim.parts)
                   or any(part is None or part.offset is None
                          or len(part.matrix) != 3 or any(len(axis) != 3 for axis in part.matrix)
                          for part in frame.parts)
                   for frame in anim.frames):
                raise ValueError("Incomplete animation frames.")
            options = {key: value for key, value in keywords.items() if key != 'use_notetrack_file'}
            result = load_anim(context, armature, anim, path, **options)
        except (OSError, ValueError, AttributeError, TypeError, IndexError, StructError) as error:
            result = str(error)

        if isinstance(result, str):
            message = "%s: %s" % (os.path.basename(path), result)
            if len(paths) == 1:
                return message
            self.report({'WARNING'}, message)
            last_error = message
        elif result:
            imported += 1
            if keywords['update_scene_fps']:
                keywords['update_scene_fps'] = False
                keywords['fps_scale_type'] = 'SCENE'

    if not imported and len(paths) > 1:
        if last_error:
            return last_error
        self.report({'INFO'}, "No new animations imported.")


def load_anim(context, armature, anim, filepath, global_scale, use_actions,
              use_actions_skip_existing, use_notetracks,
              fps_scale_type, fps_scale_target_fps, update_scene_fps, anim_offset):
    action_name = os.path.splitext(os.path.basename(filepath))[0]
    if use_actions and use_actions_skip_existing and action_name in bpy.data.actions:
        return False

    # Exact matches win when the rig has multiple names differing only by case.
    part_indices = {part.name: index for index, part in enumerate(anim.parts)}
    folded_indices = {}
    for name, index in part_indices.items():
        folded = name.casefold()
        folded_indices[folded] = index if folded not in folded_indices else None
    bones = sorted(armature.pose.bones, key=lambda bone: len(bone.parent_recursive))
    mapped = {bone.name: part_indices.get(bone.name, folded_indices.get(bone.name.casefold()))
              for bone in bones}
    if all(index is None for index in mapped.values()):
        return "No animation parts match the active armature."

    scene = context.scene
    if update_scene_fps:
        scene.render.fps = max(1, min(32767, round(anim.framerate)))
        scene.render.fps_base = scene.render.fps / anim.framerate
    if fps_scale_type == 'SCENE':
        fps_scale_target_fps = scene.render.fps / scene.render.fps_base
    frame_scale = 1.0 if fps_scale_type == 'DISABLED' else fps_scale_target_fps / anim.framerate

    data = armature.animation_data_create()
    if use_actions:
        action = bpy.data.actions.new(action_name)
        action.use_fake_user = True
        data.action = action

    previous_rotations = {}
    for frame in anim.frames:
        time = frame.frame * frame_scale + anim_offset
        pose_matrices = {}
        for bone in bones:
            parent = bone.parent
            parent_args = {}
            if parent:
                parent_args = {'parent_matrix': pose_matrices[parent.name],
                               'parent_matrix_local': parent.bone.matrix_local}
            rest = bone.bone.matrix_local
            part_index = mapped[bone.name]
            convert = bone.bone.convert_local_to_pose
            if part_index is None:
                pose_matrices[bone.name] = convert(bone.matrix_basis, rest, **parent_args)
                continue

            part = frame.parts[part_index]
            pose = Matrix(part.matrix).transposed().to_4x4()
            pose.translation = Vector(part.offset) * global_scale
            pose_matrices[bone.name] = pose
            bone.matrix_basis = convert(pose, rest, invert=True, **parent_args)
            previous = previous_rotations.get(bone.name)
            if bone.rotation_mode == 'QUATERNION':
                rotation_path = 'rotation_quaternion'
                if previous is not None:
                    rotation = bone.rotation_quaternion.copy()
                    rotation.make_compatible(previous)
                    bone.rotation_quaternion = rotation
            elif bone.rotation_mode == 'AXIS_ANGLE':
                rotation_path = 'rotation_axis_angle'
            else:
                rotation_path = 'rotation_euler'
                if previous is not None:
                    rotation = bone.rotation_euler.copy()
                    rotation.make_compatible(previous)
                    bone.rotation_euler = rotation
            if rotation_path != 'rotation_axis_angle':
                previous_rotations[bone.name] = getattr(bone, rotation_path).copy()
            for path in ('location', rotation_path, 'scale'):
                bone.keyframe_insert(path, frame=time)

    if use_notetracks:
        for note in anim.notes:
            time = round(note.frame * frame_scale + anim_offset)
            if use_actions:
                marker = action.pose_markers.new(note.string)
                marker.frame = time
            else:
                scene.timeline_markers.new(note.string, frame=time)

    context.view_layer.update()
    return True
