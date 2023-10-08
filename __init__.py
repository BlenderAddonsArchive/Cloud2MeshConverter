bl_info = {
    "name": "c2m",
    "blender": (3, 60, 4),
    "description": "Converts pointcloud to textured mesh.",
    "location": "Right side panel > \"c2m\" tab",
    "category": "c2m",
}


import sys
import os
import subprocess
import site
import importlib

libraries_to_install = ["open3d", "laspy", "numpy", "tqdm"]

auto_import = True

python_exe = sys.executable
target = site.getsitepackages()[0]

subprocess.call([python_exe, '-m', 'ensurepip'])
subprocess.call([python_exe, '-m', 'pip', 'install', '--upgrade', 'pip'])


if auto_import:

    for library in libraries_to_install:
        try:
            globals()[library] = __import__(library)  # Versuche, die Bibliothek zu importieren
        except ImportError:
            # Wenn der Import fehlschlägt, installiere die Bibliothek
            import subprocess
            subprocess.call([python_exe, '-m', 'pip', 'install', library])
            # Versuche den Import erneut
            globals()[library] = __import__(library)

else:
    import open3d
    import laspy
    import numpy
    import tqdm
     
o3d = open3d

import bpy
import bmesh
import threading
import time
import numpy as np
import math
from typing import Tuple
from pathlib import Path
import curses

from bpy.types import Scene
from bpy.types import Panel
from bpy.types import Operator
from bpy.props import StringProperty, BoolProperty, IntProperty, FloatProperty


pointcloud = None


def texture_mesh(self, context):
    global pointcloud

    if pointcloud is None:
        self.report({'ERROR'}, "Pointcloud is None.")
        return {'CANCELLED'}

    # go to object mode
    if bpy.context.mode == 'EDIT_MESH':
        bpy.ops.object.mode_set(mode="OBJECT")

    if context.view_layer.objects.active: 
            
        # give some time to update gui
        time.sleep(0.1)
        
        # self.report({'INFO'},"This may take some time")
        
        print("prepare texturing...")
        
        mesh_object = context.view_layer.objects.active
            
        bm = bmesh.new()    
        bm.from_mesh(mesh_object.data)
        
        # properties    
        output_name = "texture"
        output_format = ".png"
        output_path = os.path.dirname(context.scene.texture_output_path)
        
        if output_path == "":
            pointcloud_path = os.path.dirname(context.scene.pointcloud_path)
            output_path = pointcloud_path
        output_path = bpy.path.abspath(output_path)
             
        if not os.path.exists(output_path) and not os.path.isdir(output_path):
            print(f"Path does not exits: {output_path}")
            return {"CANCELLED"}      
        
        output_path = os.path.join(output_path, output_name + output_format)   
        
        color_search_radius = context.scene.color_search_radius
        color_max_neighbors = context.scene.color_max_neighbors
        tex_size = context.scene.texture_size
        sub_pixels = context.scene.texture_sub_pixels
        
        
        # get active mesh
        vertices = []
        for vert in bm.verts:
            x, y, z = vert.co
            vertices.extend([x, y, z])
        
        vert_count = int(len(vertices) / 3)
        vertices = np.asarray(vertices).reshape((vert_count, 3))
        
        triangles = []
        triangle_uvs = []
        
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            bpy.ops.object.mode_set(mode="EDIT")
            # Der UV-Layer existiert nicht, also führen Sie eine Smart UV Projection aus
            bpy.ops.uv.smart_project()
            bpy.ops.object.mode_set(mode="OBJECT")
            
            bm = bmesh.new()
            bm.from_mesh(mesh_object.data)
            uv_layer = bm.loops.layers.uv.active
        
        for face in bm.faces: 
            for loop in face.loops:
                vertex_index = loop.vert.index
                triangles.append(vertex_index)
                triangle_uvs.extend(loop[uv_layer].uv)
        
        triangle_count = int(len(triangles) / 3)
        triangles = np.asarray(triangles).reshape((triangle_count, 3))
        triangle_uvs = np.asarray(triangle_uvs).reshape((3 * triangle_count, 2))
        
        # TODO: Downsample Pointcloud
        
        #size = context.scene.texturing_pointcloud_size
        #point_count = int(len(np.asarray(pointcloud.points)))
        #ratio = max(0.0, min(size / point_count, 1.0))
        #pointcloud_down_sampled = pointcloud.random_down_sample(ratio)
        
        
        tree = o3d.geometry.KDTreeFlann(pointcloud)
        point_colors = np.asarray(pointcloud.colors)

        width = tex_size
        height = tex_size
        pixel_width = 1 / width
        pixel_height = 1 / height

        subpixel_width = 1 / math.sqrt(sub_pixels)
        subpixel_height = 1 / math.sqrt(sub_pixels)

        colors = np.zeros((width, height, 3))

        subpixel_hits = np.zeros((width, height), dtype=np.int32)

        # utility functions
        def barycentric(px: float, py: float, ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> Tuple[float, float, float]:
            v0x = bx - ax
            v0y = by - ay
            v1x = cx - ax
            v1y = cy - ay
            v2x = px - ax
            v2y = py - ay
            den = v0x * v1y - v1x * v0y
            if den != 0.0:
                v = (v2x * v1y - v1x * v2y) / den
                w = (v0x * v2y - v2x * v0y) / den
                u = 1.0 - v - w
                return u, v, w
            else:
                return -1.0, -1.0, -1.0
            
        def map_to_range(value: float, value_min: float, value_max: float, range_min: float, range_max: float) -> float:
            return range_min + (float(value - value_min) / float(value_max - value_min) * (range_max - range_min))

        for i, triangle in tqdm.tqdm(enumerate(triangles), total=triangle_count, desc="Texturing"):
            
            # calculate bounding box
            min_u = np.min(triangle_uvs[3 * i:3 * i + 3, 0])
            max_u = np.max(triangle_uvs[3 * i:3 * i + 3, 0])
            min_v = np.min(triangle_uvs[3 * i:3 * i + 3, 1])
            max_v = np.max(triangle_uvs[3 * i:3 * i + 3, 1])

            min_u_pixel = max(0, math.floor(min_u * width) - 1)
            max_u_pixel = min(width, math.floor(max_u * width) + 1)
            min_v_pixel = max(0, math.floor(min_v * height) - 1)
            max_v_pixel = min(height, math.floor(max_v * height) + 1)

            # for each pixel
            for act_height in range(min_v_pixel, max_v_pixel):
                for act_width in range(min_u_pixel, max_u_pixel):

                    color = np.array([0.0, 0.0, 0.0])
                    normal = np.array([0.0, 0.0, 0.0])

                    # for each subpixel
                    for p_h in range(int(math.sqrt(sub_pixels))):
                        for p_w in range(int(math.sqrt(sub_pixels))):
                            
                            # iterate over every corners of a pixel
                            p = [(act_width + (p_w + 0.5) * subpixel_width) * pixel_width,
                                 (act_height + (p_h + 0.5) * subpixel_height) * pixel_height]
                            
                            pixel_positions = [p]
                            
                            if context.scene.texture_pixel_corners:
                                for corner in range(4):
                                    p = [(act_width + (p_w + 2.0*(corner % 2) * 0.5) * subpixel_width) * pixel_width,
                                                 (act_height + (p_h + 2.0*(corner // 2) * 0.5) * subpixel_height) * pixel_height]
                                    pixel_positions.append(p)
                                                        
                            for pixel_pos in pixel_positions:

                                #pixel_pos = [(act_width + (p_w + (corner % 2) * 0.5) * subpixel_width) * pixel_width,
                                #             (act_height + (p_h + (corner // 2) * 0.5) * subpixel_height) * pixel_height]

                                alpha, beta, gamma = barycentric(pixel_pos[0], pixel_pos[1],
                                                                 triangle_uvs[3 * i + 0][0], triangle_uvs[3 * i + 0][1],
                                                                 triangle_uvs[3 * i + 1][0], triangle_uvs[3 * i + 1][1],
                                                                 triangle_uvs[3 * i + 2][0], triangle_uvs[3 * i + 2][1])

                                if 0.0 <= alpha <= 1.0 and 0.0 <= beta <= 1.0 and 0.0 <= gamma <= 1.0:

                                    # if barycentric coordinates are positive the pixel position lays within the triangle
                                    v_a = vertices[triangles[i][0]]
                                    v_b = vertices[triangles[i][1]]
                                    v_c = vertices[triangles[i][2]]

                                    pos = alpha * v_a + beta * v_b + gamma * v_c

                                    # colors
                                    v, n_vertices, n_distances = tree.search_hybrid_vector_3d(query=pos,
                                                                                              radius=color_search_radius,
                                                                                              max_nn=5)

                                    # just take the nearest vertex if no neighbours in search radius were found
                                    if not len(n_vertices):
                                        nearest = tree.search_knn_vector_3d(query=pos, knn=1)[1][0]
                                        color += np.copy(point_colors[nearest])

                                    else:
                                        weights = np.array(
                                            [map_to_range(n_distances[j], 0.0, color_search_radius, 1.0, 0.0) for j in
                                             range(len(n_vertices))])
                                        # The weights of all neighbors should sum up to 1, this way we keep the initial color brightness
                                        weights_normalized = weights / np.sum(weights)
                                        for j in range(len(n_vertices)):
                                            color += point_colors[n_vertices[j]] * weights_normalized[j]

                                    subpixel_hits[act_height, act_width] += 1

                    colors[act_height, act_width] += color

        nonzero_indices = subpixel_hits != 0

        colors[nonzero_indices] /= subpixel_hits[nonzero_indices][:, None]
        colors[nonzero_indices] *= 255
        colors = colors.astype(np.uint8)


        color_texture = o3d.geometry.Image(colors)
        o3d.io.write_image(str(output_path), color_texture.flip_vertical())
        
        # create material with new texture
        mat = bpy.data.materials.new(name="MyMaterial")
        mesh_object.data.materials.append(mat)
        
        mat.use_nodes = True
        nodes = mat.node_tree.nodes

        output_node = nodes.get("Material Output")

        # Neuen Texturknoten erstellen und zuweisen
        texture_node = nodes.new(type='ShaderNodeTexImage')
        texture_node.location = (-200, 0)  # Anpassen der Position des Knotens

        # Bild für die Textur laden
        texture_node.image = bpy.data.images.load(output_path)

        # Verbinden Sie den Texturknoten mit dem Materialausgabeknoten
        mat.node_tree.links.new(texture_node.outputs["Color"], output_node.inputs["Surface"])

    
        return {'FINISHED'}
    else:
        self.report({'ERROR'}, "No active mesh.")
        return {'CANCELLED'}


class TextureMesh(Operator):
    bl_idname = "c2m.texturing_mesh"
    bl_label = "Calculate Texture"
    
    def execute(self, context):

        result =  {'FINISHED'}
        print("TEXTURE MESH")
        try:
            result = texture_mesh(self, context)
        except Exception as e:
            print(e)
        finally:
            print("Done")
            return result 

        
        return result




def triangulate_pointcloud(self, context):
    global pointcloud
    
    if pointcloud is None:
        self.report({'ERROR'}, "Pointcloud is None.")
        return {'CANCELLED'}
    
    # give some time to update gui
    time.sleep(0.1)
    
    # properties
    size = context.scene.pointcloud_downsampling_size
    depth = context.scene.triangulation_depth
    scale = context.scene.triangulation_scale
    removal_threshold = context.scene.triangulation_removal_threshold
    
    
    # downsampling pointcloud
    print("downsampling pointcloud...")
    point_count = int(len(np.asarray(pointcloud.points)))
    ratio = max(0.0, min(size / point_count, 1.0))
    pointcloud_down_sampled = pointcloud.random_down_sample(ratio)
    
    # calculate normals
    print("calculating normals...")
    pointcloud_down_sampled.estimate_normals(fast_normal_computation=True)
    pointcloud_down_sampled.orient_normals_consistent_tangent_plane(8)
    pointcloud_down_sampled.normals = o3d.utility.Vector3dVector(np.asarray(pointcloud_down_sampled.normals) * -1)
    
    
    # triangulation
    print("triangulating pointcloud...")
    mesh = o3d.geometry.TriangleMesh()
    mesh, densities = mesh.create_from_point_cloud_poisson(
        pcd=pointcloud_down_sampled,
        depth=depth,
        scale=scale,
        linear_fit=True
    )
    
    print("finished open3d triangulation")
    
    vertices = np.asarray(mesh.vertices)
    edges = []
    faces = np.asarray(mesh.triangles)
    
    blender_mesh = bpy.data.meshes.new('mesh')
    blender_mesh.from_pydata(vertices, edges, faces)
    blender_mesh.update()

    
    # add object to scene
    mesh_object = bpy.data.objects.new('mesh', blender_mesh)
    collection = bpy.data.collections.get(context.scene.collection_name)
    if collection is None:
        collection = bpy.data.collections.new(context.scene.collection_name)
    collection.objects.link(mesh_object) 
    
    context.view_layer.objects.active = mesh_object

    bm = bmesh.new()
    bm.from_mesh(mesh_object.data) 

    # add density property to my mesh
    density_layer = bm.verts.layers.float.new('density')
    bm.verts.ensure_lookup_table()
    for vert in bm.verts:
        vert.select_set(False) 
        density = densities[vert.index]
        bm.verts[vert.index][density_layer] = density  
    
    bm.to_mesh(mesh_object.data)    
    
    bm.free()
    del mesh
    
    return {'FINISHED'}

class TriangulatePointCloud(Operator):
    bl_idname = "c2m.triangulate_point_cloud"
    bl_label = "Triangulate Pointcloud"
    
    
    def execute(self, context):
        
        result =  {'FINISHED'}
        print("TRIANGULATE POINTCLOUD")
        try:
            result = triangulate_pointcloud(self, context)
        except Exception as e:
            print(e)
        finally:
            print("Done")
            return result 

        
        return result

def read_pointcloud(self, context):
    global pointcloud
    
    # help python to free some memory
    if pointcloud is not None:
        del pointcloud
    
    path = bpy.path.abspath(context.scene.pointcloud_path)
    print(path) 
    
    cloud = o3d.geometry.PointCloud()
    
    if path.split(".")[-1] == "las" or path.split(".")[-1] == "laz":
        with laspy.open(path) as file:
            dims = []
            h = file.header
            for dimension in h.point_format.dimensions:
                dims.append(dimension.name)

            # load data chunk wise
            for data_chunk in file.chunk_iterator(10_000):

                points = np.ascontiguousarray(
                    np.vstack((data_chunk.x, data_chunk.y, data_chunk.z)).transpose(),
                    dtype=np.float64)
                # scale cloud to unit cube
                # max_len = max(file.header.x_max, max(file.header.y_max, file.header.z_max))
                cloud.points.extend(o3d.utility.Vector3dVector(points))

                if 'red' in dims and 'green' in dims and 'blue' in dims:
                    colors = np.ascontiguousarray(
                        np.vstack((data_chunk.red, data_chunk.green, data_chunk.blue)).transpose(), dtype=np.float64)
                    cloud.colors.extend(o3d.utility.Vector3dVector(colors / 65535.0))

            cloud.translate(-np.array([h.x_offset, h.y_offset, h.z_offset]))
    else:
        cloud = o3d.io.read_point_cloud(path)
    
    pointcloud = cloud
    
    # add new collection if it doesn't exist yet
    collection = bpy.data.collections.get(context.scene.collection_name)
    if collection is None:
        collection = bpy.data.collections.new(context.scene.collection_name)
        bpy.context.scene.collection.children.link(collection)
    
    return {'FINISHED'}


class ReadPointCloud(Operator):
    bl_idname = "c2m.read_point_cloud"
    bl_label = "Read Pointcloud"
    
    @classmethod
    def poll(cls, context):
        return True
        #return (pointcloud is not None and not context.scene.is_triangulating_pointcloud and not context.scene.is_texturing_mesh)
        
    def execute(self, context):
    
        result =  {'FINISHED'}
        print("READ POINTCLOUD")
        try:
            result = read_pointcloud(self, context)
        except Exception as e:
            print(e)
        finally:
            print("Done")
            return result 

        
        return result
    

class DecimateGeometry(Operator):
    bl_idname = "c2m.decimate_geometry"
    bl_label = "Decimate geometry"
            
    def execute(self, context):
        if context.view_layer.objects.active:
            mesh_object = context.view_layer.objects.active
            if mesh_object.type != 'MESH':
                return {'CANCELLED'}
            bpy.ops.object.mode_set(mode="EDIT") 
            

        
        return {'FINISHED'} 
        
def remove_vertices(self, context):
    
    if context.view_layer.objects.active:
        mesh_object = context.view_layer.objects.active
        
        bpy.ops.object.mode_set(mode="EDIT") 
          
        bm = bmesh.from_edit_mesh(mesh_object.data) 
        
        density_layer = bm.verts.layers.float.get('density') 

        removal_threshold = context.scene.triangulation_removal_threshold
        
        if density_layer:
            bpy.ops.mesh.select_mode(type="VERT")
            bpy.ops.mesh.select_all(action='DESELECT')
            
            # fetch density values
            densities = []
            bm.verts.ensure_lookup_table()
            densities = [bm.verts[vert.index][density_layer] for vert in bm.verts]
            
            # quantile (removing a certain percentage)
            vertices_to_remove = densities < np.quantile(densities, removal_threshold)
            for vert in bm.verts:   
                if vertices_to_remove[vert.index] != 0:
                    bm.verts[vert.index].select_set(True)    

            bmesh.update_edit_mesh(mesh_object.data)
            
            bm.select_flush(True)
            bpy.ops.mesh.select_mode(type="FACE")
            
        else:
            print("Mesh has no density layer.")
            return 
                
    else:
        print("No active mesh.")
        return
        


class Cloud2MeshPanel(bpy.types.Panel):
    """ Settings for conversion """
    bl_label = "Cloud2Mesh"
    bl_idname = "cloud_to_mesh"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "c2m"
    
    
    def draw(self, context):
        global pointcloud
        
        layout = self.layout
        col = layout.column()

        # read pointcloud
        col.prop(context.scene, "pointcloud_path", text="pointcloud path")
        col.operator("c2m.read_point_cloud")
        
        if pointcloud is not None:
            path = os.path.basename(context.scene.pointcloud_path)
            name = os.path.splitext(path)[0]
            
            box1 = col.box()
            box1.label(text=f"Name: {name}")
            box1.label(text=f"Points: {len(np.asarray(pointcloud.points))}")    
            col.separator()   

        
            # triangulate pointcloud
            col.operator("c2m.triangulate_point_cloud")

            # texuting mesh
            col.operator("c2m.texturing_mesh")
        

class UtilityPanel(bpy.types.Panel):
    """ Settings for conversion """
    bl_label = "Utility"
    bl_idname = "utility"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "c2m"
    
    def draw(self, context):
        row = self.layout.row()
        row.label(text="Vertex removal threshold:")
        row.prop(context.scene, "triangulation_removal_threshold", text="")
        
        #row2 = self.layout.row()
        #row2.label(text="Decimate geometry:")
        #row2.operator("c2m.decimate_geometry")
        

class SettingsPanel(bpy.types.Panel):
    """ Settings for conversion """
    bl_label = "Settings"
    bl_idname = "settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "c2m"
    bl_options = {'DEFAULT_CLOSED'}
    
    
    def draw(self, context):
        layout = self.layout
        
        #box1 = layout.box()
        #box1.label(text="Pointcloud")
        #box1.prop(context.scene, "visualize_pointcloud", text="Visualize Pointcloud on load")
        
        box2 = layout.box()
        box2.label(text="Triangulation")
        box2.prop(context.scene, "pointcloud_downsampling_size", text="Pointcloud downsampling size")
        box2.prop(context.scene, "triangulation_depth", text="Triangulation depth")
        box2.prop(context.scene, "triangulation_scale", text="Triangulation scale")
        
        box3 = layout.box()
        box3.label(text="Texturing")
        box3.prop(context.scene, "texture_output_path", text="Texture output path")
        box3.prop(context.scene, "texture_size", text="Texture size")
        box3.prop(context.scene, "texture_sub_pixels", text="Texture sub pixels")
        # box3.prop(context.scene, "texturing_pointcloud_size", text="Texturing pointcloud size")
        box3.prop(context.scene, "color_search_radius", text="Color search radius")
        box3.prop(context.scene, "color_max_neighbors", text="Color max neighbors")
        box3.prop(context.scene, "texture_pixel_corners", text="pixel corners")

    
classes = (
        Cloud2MeshPanel,
        UtilityPanel,
        SettingsPanel,
        ReadPointCloud,
        TriangulatePointCloud,
        TextureMesh,
        DecimateGeometry
    )

def register():
    from bpy.utils import register_class
    for cls in classes:
        register_class(cls)
    Scene.collection_name = StringProperty(name="collection_name", default="Converter Collection")
    Scene.pointcloud_path = StringProperty(name="pointcloud_path", subtype="FILE_PATH")
    
    # Pointcloud properties
    Scene.visualize_pointcloud = BoolProperty(name="visualize_pointcloud", default=False)
    
    # Triangulation properties
    Scene.pointcloud_downsampling_size = IntProperty(name="pointcloud_downsampling_size", default=100_000)
    Scene.triangulation_depth = IntProperty(name="triangulation_depth", default=11)
    Scene.triangulation_scale = FloatProperty(name="triangulation_scale", default=1.1)
    Scene.triangulation_removal_threshold = FloatProperty(name="vertex_removal_threshold", default=0.05, soft_min=0.0, soft_max=1.0, description="Removal Slider", step=0.5,precision=3, update=remove_vertices)
    
    # Texturing
    Scene.texture_output_path = StringProperty(name="texture_output_path", subtype="DIR_PATH", default="")
    Scene.color_search_radius = IntProperty(name="color_search_radius", default=1)
    Scene.color_max_neighbors = IntProperty(name="color_max_neighbors", default=1)
    Scene.texture_size = IntProperty(name="texture_size", default=1024)
    Scene.texture_sub_pixels = IntProperty(name="texture_sub_pixels", default=1)
    Scene.texture_pixel_corners = BoolProperty(name="texture_pixel_corners", default=True)
    Scene.texturing_pointcloud_size = IntProperty(name="texturing_pointcloud_size", default=1_000_000)
    
    
    
def unregister():
    from bpy.utils import unregister_class
    for cls in classes:
        unregister_class(cls)
    del Scene.pointcloud_path
    del Scene.collection_name
    
    del Scene.visualize_pointcloud
    
    del Scene.triangulation_depth
    del Scene.triangulation_scale
    del Scene.triangulation_removal_threshold

    del Scene.texture_output_path
    del Scene.color_search_radius
    del Scene.color_max_neighbors
    del Scene.texture_size
    del Scene.texture_sub_pixels
    del Scene.texture_pixel_corners
    del Scene.texturing_pointcloud_size
    
    global pointcloud
    if pointcloud:
        del pointcloud
    
    
    
if __name__ == "__main__":
    register()
