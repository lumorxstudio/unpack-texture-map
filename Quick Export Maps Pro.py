bl_info = {
    "name": "Quick Export Maps Pro (Material Sync)",
    "blender": (4, 2, 0),
    "category": "Material",
    "author": " Lumorx Studio ",
    "version": (1, 6),
    "description": "Export textures by material with presets (Unreal, Unity, Packed MRAO, Full PBR)",
}

import bpy
import os
import numpy as np

# =========================
#   Helpers / Settings
# =========================

EXT_MAP = {
    "PNG": "png",
    "JPEG": "jpg",
    "TARGA": "tga",
    "BMP": "bmp",
    "TIFF": "tif",
}

def update_preset(self, context):
    preset = self.preset
    if preset == 'UNREAL_PBR':
        self.image_format = 'TARGA'
        self.prefix = ""
        self.suffix = ""
    elif preset == 'UNITY_HDRP':
        self.image_format = 'PNG'
        self.prefix = ""
        self.suffix = ""
    elif preset == 'PACKED_MRAO':
        self.image_format = 'TARGA'
        self.prefix = "packed_"
        self.suffix = ""
    else:
        self.image_format = 'PNG'
        self.prefix = ""
        self.suffix = ""

class QEMPProperties(bpy.types.PropertyGroup):
    preset: bpy.props.EnumProperty(
        name="Export Preset",
        items=[
            ('DEFAULT', "Default", "Preset mặc định"),
            ('UNREAL_PBR', "Unreal Engine PBR", "Xuất maps chuẩn Unreal"),
            ('UNITY_HDRP', "Unity HDRP", "Xuất maps chuẩn Unity HDRP"),
            ('PACKED_MRAO', "Packed MRAO", "Packed Metallic-Roughness-AO")
        ],
        default='DEFAULT',
        update=update_preset
    )

    directory: bpy.props.StringProperty(
        name="Export Path",
        subtype='DIR_PATH',
        default="//exported_maps/"
    )
    prefix: bpy.props.StringProperty(name="Prefix", default="")
    suffix: bpy.props.StringProperty(name="Suffix", default="")
    image_format: bpy.props.EnumProperty(
        name="Format",
        items=[
            ('PNG', "PNG", ""),
            ('JPEG', "JPEG", ""),
            ('TARGA', "TARGA", ""),
            ('BMP', "BMP", ""),
            ('TIFF', "TIFF", "")
        ],
        default='PNG'
    )

# =========================
#   Export Helpers
# =========================

def export_image(image, mat_name, suffix, props, export_dir, operator=None):
    """Lưu 1 ảnh texture ra file"""
    if not image:
        return
    ext = EXT_MAP.get(props.image_format, props.image_format.lower())
    safe_mat = mat_name.replace('.', '_')
    filename = f"{props.prefix}{safe_mat}_{suffix}{props.suffix}.{ext}"
    filepath = os.path.join(export_dir, filename)

    try:
        # set absolute path for Blender
        image.filepath_raw = bpy.path.abspath(filepath)
        image.file_format = props.image_format
        image.save()
        if operator:
            operator.report({'INFO'}, f"✅ Xuất {suffix}: {filepath}")
    except Exception as e:
        if operator:
            operator.report({'ERROR'}, f"❌ Lỗi khi lưu {suffix}: {filepath} ({str(e)})")

def export_packed_mrao(mat, mat_dir, props, operator=None):
    """Tạo ảnh packed M=R=A (R->R, G->Roughness, B->AO)"""
    if not getattr(mat, "node_tree", None):
        if operator:
            operator.report({'WARNING'}, f"Material {mat.name} không có node tree.")
        return

    nodes = mat.node_tree.nodes

    def find_image_map(keyword):
        for node in nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                if keyword in (node.image.name or "").upper() or keyword in (node.name or "").upper() or keyword in (node.label or "").upper():
                    return node.image
        return None

    img_metallic = find_image_map("METAL")
    img_roughness = find_image_map("ROUGH")
    img_ao = find_image_map("AO")

    if not any([img_metallic, img_roughness, img_ao]):
        if operator:
            operator.report({'WARNING'}, f"Không tìm thấy METAL / ROUGH / AO trong {mat.name}")
        return

    base_img = img_metallic or img_roughness or img_ao
    width, height = base_img.size

    for img in (img_metallic, img_roughness, img_ao):
        if img and img.size != (width, height):
            if operator:
                operator.report({'WARNING'}, f"Kích thước ảnh không khớp trong {mat.name}. Bỏ packing.")
            return

    packed_img = bpy.data.images.new(
        name=f"{mat.name}_Packed_MRAO",
        width=width,
        height=height,
        alpha=False,
        float_buffer=False
    )

    def get_gray_pixels(image):
        if not image:
            return np.zeros((width * height,), dtype=float)
        raw = list(image.pixels[:])
        if len(raw) < width * height * 4:
            return np.zeros((width * height,), dtype=float)
        arr = np.array(raw, dtype=float)
        return arr[0::4]

    r_chan = get_gray_pixels(img_metallic)
    g_chan = get_gray_pixels(img_roughness)
    b_chan = get_gray_pixels(img_ao)

    new_pixels = np.empty((width * height * 4,), dtype=float)
    new_pixels[0::4] = r_chan
    new_pixels[1::4] = g_chan
    new_pixels[2::4] = b_chan
    new_pixels[3::4] = 1.0

    packed_img.pixels = new_pixels.tolist()

    ext = EXT_MAP.get(props.image_format, props.image_format.lower())
    filename = f"{props.prefix}{mat.name.replace('.', '_')}_packedMRAO{props.suffix}.{ext}"
    filepath = os.path.join(mat_dir, filename)

    try:
        packed_img.filepath_raw = bpy.path.abspath(filepath)
        packed_img.file_format = props.image_format
        packed_img.save()
        if operator:
            operator.report({'INFO'}, f"✅ Xuất packed MRAO: {filepath}")
    except Exception as e:
        if operator:
            operator.report({'ERROR'}, f"❌ Lỗi khi lưu packed MRAO {filepath}: {str(e)}")
    finally:
        try:
            bpy.data.images.remove(packed_img)
        except Exception:
            pass

# =========================
#   Export Main
# =========================

def export_maps(objects, props, operator=None):
    base_export_dir = bpy.path.abspath(props.directory)
    os.makedirs(base_export_dir, exist_ok=True)

    exported_mats = set()
    for obj in objects:
        if not getattr(obj, "material_slots", None):
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if not mat or not getattr(mat, "use_nodes", False):
                continue
            if mat.name in exported_mats:
                continue
            exported_mats.add(mat.name)

            # Thư mục riêng cho từng material
            mat_dir = os.path.join(base_export_dir, mat.name.replace(".", "_"))
            os.makedirs(mat_dir, exist_ok=True)

            if props.preset == "PACKED_MRAO":
                export_packed_mrao(mat, mat_dir, props, operator)
            else:
                # Lấy tất cả TEX_IMAGE node (không cần nối vào BSDF)
                if not getattr(mat, "node_tree", None):
                    continue

                tex_nodes = [n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and getattr(n, "image", None)]
                exported_images = set()  # tránh xuất trùng cùng 1 image nhiều lần

                for node in tex_nodes:
                    img = node.image
                    if not img:
                        continue
                    # tránh trùng
                    if img.name in exported_images:
                        continue
                    exported_images.add(img.name)

                    # kiểm tra cả image.name, node.name và node.label để bắt tên
                    checks = " ".join([
                        (img.name or "").lower(),
                        (node.name or "").lower(),
                        (node.label or "").lower()
                    ])

                    if any(k in checks for k in ("base", "albedo", "diffuse")):
                        export_image(img, mat.name, "BaseColor", props, mat_dir, operator)
                    elif "normal" in checks:
                        export_image(img, mat.name, "Normal", props, mat_dir, operator)
                    elif "rough" in checks:
                        export_image(img, mat.name, "Roughness", props, mat_dir, operator)
                    elif "metal" in checks:
                        export_image(img, mat.name, "Metallic", props, mat_dir, operator)
                    elif any(k in checks for k in ("ao", "occlusion", "ambient")):
                        export_image(img, mat.name, "AO", props, mat_dir, operator)
                    elif any(k in checks for k in ("emis", "emit")):
                        export_image(img, mat.name, "Emissive", props, mat_dir, operator)
                    elif any(k in checks for k in ("height", "disp", "displacement")):
                        export_image(img, mat.name, "Height", props, mat_dir, operator)
                    elif any(k in checks for k in ("alpha", "opacity", "trans")):
                        export_image(img, mat.name, "Opacity", props, mat_dir, operator)
                    elif "spec" in checks:
                        export_image(img, mat.name, "Specular", props, mat_dir, operator)
                    else:
                        # Nếu không nằm trong danh sách trên, xuất theo tên gốc (tiện cho trường hợp custom)
                        # Bạn có thể bỏ dòng này nếu không muốn xuất file lạ
                        safe_name = (img.name or "texture").replace(" ", "_")
                        export_image(img, mat.name, safe_name, props, mat_dir, operator)

# =========================
#   Operators
# =========================

class QEMP_OT_export_selected(bpy.types.Operator):
    bl_idname = "qemp.export_selected"
    bl_label = "Export Selected Objects"

    def execute(self, context):
        props = context.scene.qemp_props
        objects = context.selected_objects
        export_maps(objects, props, self)
        return {'FINISHED'}

class QEMP_OT_export_all(bpy.types.Operator):
    bl_idname = "qemp.export_all"
    bl_label = "Export All Objects"

    def execute(self, context):
        props = context.scene.qemp_props
        objects = bpy.context.scene.objects
        export_maps(objects, props, self)
        return {'FINISHED'}

# =========================
#   UI Panel (N-Panel)
# =========================

class QEMP_PT_panel(bpy.types.Panel):
    bl_label = "Quick Export Maps Pro"
    bl_idname = "QEMP_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Quick Tools'

    def draw(self, context):
        layout = self.layout
        props = context.scene.qemp_props

        layout.prop(props, "preset")
        layout.prop(props, "directory")
        layout.prop(props, "image_format")
        layout.prop(props, "prefix")
        layout.prop(props, "suffix")

        layout.separator()
        layout.operator("qemp.export_selected", icon="EXPORT")
        layout.operator("qemp.export_all", icon="FILE_FOLDER")

# =========================
#   Register
# =========================

classes = [
    QEMPProperties,
    QEMP_OT_export_selected,
    QEMP_OT_export_all,
    QEMP_PT_panel
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.qemp_props = bpy.props.PointerProperty(type=QEMPProperties)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.qemp_props

if __name__ == "__main__":
    register()
