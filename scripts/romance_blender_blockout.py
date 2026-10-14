"""CPU spatial references, adapted from agent-skill/blockout_blender.py.

Only geometry, camera and placed cast are reused. The ancient lecture room,
floor-cushion figures and automatic extras are replaced by the supplied set.
Run through Blender: blender -b -P this_file -- manifest.json output_dir
"""
from pathlib import Path
import json
import math
import sys

import bpy
from mathutils import Vector

MATERIALS = {}


def material(gray):
    if gray not in MATERIALS:
        mat = bpy.data.materials.new(f'clay-{gray}')
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes['Principled BSDF']
        bsdf.inputs['Base Color'].default_value = (gray, gray, gray, 1)
        bsdf.inputs['Roughness'].default_value = .85
        MATERIALS[gray] = mat
    return MATERIALS[gray]


def finish(name, gray):
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(material(gray))
    return obj


def box(name, low, high, gray=.75):
    bpy.ops.mesh.primitive_cube_add(size=1, location=tuple((a+b)/2 for a,b in zip(low, high)))
    bpy.context.object.scale = tuple(b-a for a,b in zip(low, high))
    return finish(name, gray)


def ball(name, position, radius, gray=.5, scale=(1,1,1)):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=20, ring_count=12, radius=radius, location=position)
    bpy.context.object.scale = scale
    return finish(name, gray)


def limb(name, start, end, radius=.055, gray=.5):
    a,b = Vector(start),Vector(end)
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=radius, depth=(b-a).length, location=(a+b)/2)
    bpy.context.object.rotation_euler = (b-a).to_track_quat('Z','Y').to_euler()
    return finish(name, gray)


def chair(name, xy, facing, target):
    before=set(bpy.data.objects)
    x,y = xy
    box(name+'-seat',(x-.34,y-.34,.40),(x+.34,y+.34,.48),.82)
    rear = y-.29*facing
    box(name+'-back',(x-.34,rear-.07,.48),(x+.34,rear+.07,1.00),.82)
    for dx in [-.30,.30]:
        box(name+'-arm',(x+dx-.035,y-.34,.63),(x+dx+.035,y+.34,.72),.68)
        for dy in [-.29,.29]:
            box(name+'-leg',(x+dx-.035,y+dy-.035,0),(x+dx+.035,y+dy+.035,.42),.68)
    # The reference chairs face the table, rather than forming a rigid grid.
    from mathutils import Matrix
    dx,dy=target[0]-x,target[1]-y
    angle=math.atan2(dy,dx)-math.atan2(facing,0)
    turn=Matrix.Rotation(angle,4,'Z')
    origin=Matrix.Translation((x,y,0))
    bpy.context.view_layer.update()
    for obj in set(bpy.data.objects)-before:
        obj.matrix_world=origin@turn@origin.inverted()@obj.matrix_world.copy()


def proxy(name, spec):
    x,y = spec['xy'];height=spec['height'];facing=spec.get('facing',-1)
    if spec['pose']=='standing':
        hip=.52*height;shoulder=.81*height;head=height-.12
        torso_bottom=(x,y,hip);torso_top=(x,y,shoulder)
        for side in [-1,1]:
            limb(name+'-leg',(x+side*.09,y,.05),(x+side*.09,y,hip),.075)
            limb(name+'-arm',(x+side*.20,y,shoulder),(x+side*.21,y+.08*facing,hip),.055)
    else:
        hip=.49;shoulder=hip+.29*height;head=hip+.43*height
        torso_bottom=(x,y,hip);torso_top=(x,y,shoulder)
        for side in [-1,1]:
            knee=(x+side*.10,y+.36*facing,.46)
            limb(name+'-thigh',(x+side*.10,y,hip),knee,.075)
            limb(name+'-calf',knee,(x+side*.10,y+.37*facing,.05),.055)
            limb(name+'-arm',(x+side*.20,y,shoulder),(x+side*.15,y+.23*facing,.69),.055)
    limb(name+'-torso',torso_bottom,torso_top,.17)
    limb(name+'-neck',torso_top,(x,y,head-.10),.045)
    ball(name+'-head',(x,y,head),.12,.5)
    ball(name+'-nose',(x,y+.12*facing,head-.02),.026,.5)


def luggage(name, xy):
    x,y=xy
    box(name+'-body',(x-.18,y-.11,.06),(x+.18,y+.11,.64),.42)
    for dx in [-.11,.11]:
        limb(name+'-handle',(x+dx,y,.64),(x+dx,y,.98),.014,.35)
    limb(name+'-grip',(x-.11,y,.98),(x+.11,y,.98),.018,.35)


def room(layout):
    # The front wall is not visible in the supplied view. Extend its floor
    # beyond the visible furniture instead of claiming a measured front wall.
    near=layout.get('near_floor_extension_m',0)
    box('floor',(0,near,-.06),(6,5,0),.78)
    box('west-wall',(-.08,0,0),(0,5,2.7),.88)
    box('north-wall-left',(0,5,0),(.70,5.08,2.7),.88)
    box('north-wall-right',(1.60,5,0),(6,5.08,2.7),.88)
    box('door-lintel',(.70,5,2.2),(1.60,5.08,2.7),.88)
    box('door',(.73,5.02,0),(1.57,5.05,2.16),.65)
    box('east-wall-low',(6,0,0),(6.08,5,.75),.88)
    box('east-wall-high',(6,0,2.25),(6.08,5,2.7),.88)
    for y in [.55,1.85,3.15,4.45]:
        box('window-mullion',(5.96,y-.035,.75),(6.04,y+.035,2.25),.64)
    for a,b in [(.55,1.85),(1.85,3.15),(3.15,4.45)]:
        box('window-glass',(6.04,a,.79),(6.05,b,2.21),.94)
    positions=[s['xy'] for s in layout['chairs'].values()]
    box('rug',(min(p[0] for p in positions)-.65,min(p[1] for p in positions)-.70,.002),
        (max(p[0] for p in positions)+.70,max(p[1] for p in positions)+.70,.015),.70)
    credenza=layout.get('credenza',{'low':[1.85,4.60,0],'high':[3.65,4.98,.62],'vase':[2.40,4.77,.77]})
    box('credenza',credenza['low'],credenza['high'],.68)
    ball('single-vase',credenza['vase'],.15,.60,scale=(1,1,1.15))
    center=layout.get('table_xy',[3.05,2.65])
    for name,spec in layout['chairs'].items():
        chair(name,spec['xy'],spec['facing'],center)
    bpy.ops.mesh.primitive_cylinder_add(vertices=64,radius=1,depth=.07,location=(*center,.42))
    bpy.context.object.scale=(*layout.get('table_radii',[.86,.49]),1)
    finish('one-oval-coffee-table-top',.62)
    bpy.ops.mesh.primitive_cylinder_add(vertices=32,radius=.25,depth=.37,location=(*center,.20))
    finish('one-coffee-table-base',.62)


def main():
    args=sys.argv[sys.argv.index('--')+1:]
    manifest=json.loads(Path(args[0]).read_text());out=Path(args[1]);out.mkdir(parents=True,exist_ok=True)
    records={}
    for cid,snapshot in manifest['blockout_snapshots'].items():
        bpy.ops.wm.read_factory_settings(use_empty=True);MATERIALS.clear()
        room(manifest['layout'])
        for name,spec in snapshot['people'].items():proxy(name,spec)
        for name,xy in snapshot['luggage'].items():luggage(name,xy)
        camera=bpy.data.objects.new('camera',bpy.data.cameras.new('camera'));bpy.context.scene.collection.objects.link(camera)
        eye=Vector(manifest['layout']['camera']['eye']);target=Vector(manifest['layout']['camera']['target'])
        camera.location=eye;camera.rotation_euler=(target-eye).to_track_quat('-Z','Y').to_euler()
        camera.data.lens=18/math.tan(math.radians(manifest['layout']['camera']['fov'])/2)
        camera.data.shift_y=-manifest['layout']['camera'].get('shift_y',0)
        camera.data.clip_start=.05;bpy.context.scene.camera=camera
        light=bpy.data.objects.new('window-light',bpy.data.lights.new('window-light','AREA'))
        bpy.context.scene.collection.objects.link(light);light.location=(5.8,2.5,2.3);light.data.energy=600;light.data.size=4
        light.rotation_euler=(Vector((2.5,2.5,.6))-light.location).to_track_quat('-Z','Y').to_euler()
        world=bpy.data.worlds.new('neutral-world');world.use_nodes=True
        world.node_tree.nodes['Background'].inputs[0].default_value=(.8,.8,.8,1)
        world.node_tree.nodes['Background'].inputs[1].default_value=.5
        scene=bpy.context.scene;scene.world=world;scene.render.engine='CYCLES';scene.cycles.device='CPU'
        scene.cycles.samples=12;scene.cycles.use_denoising=True
        scene.render.threads_mode='FIXED';scene.render.threads=8
        scene.render.resolution_x,scene.render.resolution_y=manifest['layout'].get('resolution',[1280,720])
        scene.render.resolution_percentage=100
        scene.view_settings.view_transform='AgX';scene.render.filepath=str(out/f'{cid}.png')
        bpy.ops.render.render(write_still=True)
        from bpy_extras.object_utils import world_to_camera_view
        reprojection={}
        for name,spec in manifest['layout']['chairs'].items():
            uv=world_to_camera_view(scene,camera,Vector((*spec['xy'],.48)))
            reprojection[name]=[uv.x*scene.render.resolution_x,(1-uv.y)*scene.render.resolution_y]
        records[cid]={'people':snapshot['people'],'luggage':snapshot['luggage'],'chairs':manifest['layout']['chairs'],
                      'camera':manifest['layout']['camera'],'chair_count':4,'table_top_m':.455,
                      'chair_seat_m':.48,'chair_seat_projection_px':reprojection,
                      'dimension_provenance':manifest['layout']['dimensions']}
    (out/'geometry.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
