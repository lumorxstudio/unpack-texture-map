bl_info = {
    "name": "Quick Unpack Pro (Full)",
    "author": "Lumorx Studio"
    "version": (1, 0),
    "blender": (4, 0, 0),
    "location": "3D View > N-Panel > Quick Tool",
    "description": "Xuất texture đã pack theo thư mục material, đặt tên Material_MapType.ext, chọn định dạng và fallback đường dẫn.",
    "category": "Material",
}

import bpy
import os
import re
import traceback

# ---------- Helpers ----------
def sanitize_filename(name: str) -> str:
    if name is None:
        return "noname"
    name = bpy.path.clean_name(str(name))
    name = re.sub(r'\s+', '_', name)
    name = re.sub(r'[^A-Za-z0-9._-]', '', name)
    if name == "":
        return "noname"
    return name

def map_type_from_link(mat, tex_node):
    """Try detect map type by following node links to sockets / normal map node"""
    try:
        nt = mat.node_tree
        for link in nt.links:
            if link.from_node == tex_node:
                to_socket = getattr(link, "to_socket", None)
                to_node = getattr(link, "to_node", None)
                socket_name = (to_socket.name if to_socket else "").lower()
                to_node_type = (to_node.type if to_node else "").lower()

                # Check normal map specially
                if "normal" in socket_name or "normal" in to_node_type or (to_node and to_node.type == 'NORMAL_MAP'):
                    return "Normal"
                if "base" in socket_name or "color" in socket_name:
                    return "BaseColor"
                if "rough" in socket_name:
                    return "Roughness"
                if "metal" in socket_name:
                    return "Metallic"
                if "alpha" in socket_name or "opacity" in socket_name:
                    return "Alpha"
        # not found: try check node label/name for hints
        name_lower = (getattr(tex_node, "label", "") or getattr(tex_node, "name", "")).lower()
        if "base" in name_lower or "albedo" in name_lower or "diffuse" in name_lower:
            return "BaseColor"
        if "norm" in name_lower:
            return "Normal"
        if "rough" in name_lower:
            return "Roughness"
        if "metal" in name_lower:
            return "Metallic"
        if "alpha" in name_lower or "opacity" in name_lower:
            return "Alpha"
    except Exception:
        pass
    return "Misc"

def ensure_unique_path(path):
    base, ext = os.path.splitext(path)
    c = 1
    new_path = path
    while os.path.exists(new_path):
        new_path = f"{base}_{c:03d}{ext}"
        c += 1
    return new_path

def safe_makedirs(path):
    """Try create directory, returns (success, used_path, message)"""
    try:
        os.makedirs(path, exist_ok=True)
        return True, path, ""
    except Exception as e:
        # detect probable network path issue on Windows
        msg = str(e).lower()
        winerr = getattr(e, "winerror", None)
        network_issue = (winerr == 53) or ("network path" in msg) or path.startswith('\\\\')
        return False, path, str(e) + (" (network?)" if network_issue else "")

# ---------- Operator ----------
class QUP_OT_export_packed_maps(bpy.types.Operator):
    bl_idname = "qup.export_packed_maps"
    bl_label = "Xuất Packed Maps"
    bl_description = "Xuất các texture đã pack theo từng thư mục material, tên: Material_MapType.ext"

    def execute(self, context):
        scene = context.scene
        raw_path = scene.qup_export_dir
        if not raw_path:
            self.report({'ERROR'}, "Chưa chọn thư mục xuất!")
            return {'CANCELLED'}

        # resolve path (support //)
        export_dir = bpy.path.abspath(raw_path)

        # attempt create
        ok, used_path, msg = safe_makedirs(export_dir)
        if not ok:
            # fallback: if .blend saved use //qup_unpacked_textures, else Desktop fallback
            print(f"[QUP] Cannot create export_dir {export_dir}: {msg}")
            if bpy.data.filepath:
                fallback = bpy.path.abspath("//qup_unpacked_textures")
            else:
                fallback = os.path.join(os.path.expanduser("~"), "Desktop", "qup_unpacked_textures")
            ok2, used2, msg2 = safe_makedirs(fallback)
            if not ok2:
                self.report({'ERROR'}, f"Không tạo được thư mục export: {msg}\nFallback failed: {msg2}")
                print("[QUP] Fallback also failed:", msg2)
                return {'CANCELLED'}
            else:
                self.report({'WARNING'}, f"Không truy cập được đường dẫn đã chọn. Sẽ xuất sang: {fallback}")
                export_dir = fallback

        fmt = scene.qup_format  # 'PNG','JPEG','TARGA'
        ext_map = {'PNG': '.png', 'JPEG': '.jpg', 'TARGA': '.tga'}
        ext = ext_map.get(fmt, '.png')

        only_selected = scene.qup_only_selected

        # gather materials to process
        mats_to_process = []
        if only_selected:
            obj = context.object
            if not obj:
                self.report({'ERROR'}, "Không có object đang chọn.")
                return {'CANCELLED'}
            # collect materials from object (active or slots)
            mats = []
            if getattr(obj, "active_material", None):
                mats.append(obj.active_material)
            for slot in getattr(obj, "material_slots", []):
                if slot.material and slot.material not in mats:
                    mats.append(slot.material)
            if not mats:
                self.report({'ERROR'}, "Object đang chọn không có material hợp lệ.")
                return {'CANCELLED'}
            mats_to_process = mats
        else:
            mats_to_process = [m for m in bpy.data.materials if m is not None]

        exported = 0
        failed = []
        exported_keys = set()  # prevent dupe exports: (mat_clean, map_type, img.name)

        for mat in mats_to_process:
            if mat is None:
                continue
            if not getattr(mat, "use_nodes", False):
                continue

            mat_name_clean = sanitize_filename(mat.name)
            mat_folder = os.path.join(export_dir, mat_name_clean)
            ok_m, used_m, msg_m = safe_makedirs(mat_folder)
            if not ok_m:
                failed.append((mat.name, f"Không tạo thư mục material: {msg_m}"))
                print(f"[QUP] Failed to create mat folder {mat_folder}: {msg_m}")
                continue

            try:
                nodes = mat.node_tree.nodes
            except Exception as e:
                failed.append((mat.name, f"Không đọc node_tree: {e}"))
                continue

            for node in nodes:
                try:
                    if node.type != 'TEX_IMAGE':
                        continue
                    img = getattr(node, "image", None)
                    if not img:
                        continue
                    if not getattr(img, "packed_file", None):
                        continue  # only packed

                    map_type = map_type_from_link(mat, node)
                    map_type_clean = sanitize_filename(map_type)

                    key = (mat_name_clean, map_type_clean, img.name)
                    if key in exported_keys:
                        continue

                    base_name = f"{mat_name_clean}_{map_type_clean}"
                    filename = base_name + ext
                    filepath = os.path.join(mat_folder, filename)
                    filepath = ensure_unique_path(filepath)

                    # set and save
                    try:
                        img.filepath_raw = filepath
                        # try set file_format safely
                        try:
                            img.file_format = fmt
                        except Exception:
                            img.file_format = 'PNG'
                        img.save()
                        exported += 1
                        exported_keys.add(key)
                    except Exception as e_img:
                        tb = traceback.format_exc()
                        failed.append((img.name, str(e_img)))
                        print(f"[QUP] Failed saving image {img.name} -> {filepath}: {e_img}")
                        print(tb)
                except Exception as e_node:
                    tb = traceback.format_exc()
                    failed.append((getattr(node, "name", "node"), str(e_node)))
                    print(f"[QUP] Node loop error in material {mat.name}: {e_node}")
                    print(tb)

        # reporting
        if exported:
            self.report({'INFO'}, f"Đã xuất {exported} texture → {export_dir}")
        else:
            self.report({'WARNING'}, "Không tìm thấy texture packed để xuất.")

        if failed:
            self.report({'WARNING'}, f"Có {len(failed)} lỗi khi xuất (xem Console).")
            print("[QUP] Failed list:")
            for fentry in failed:
                print(fentry)

        return {'FINISHED'}


# ---------- UI Panel in N-Panel ----------
class QUP_PT_panel(bpy.types.Panel):
    bl_label = "Quick Unpack"
    bl_idname = "QUP_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Quick Tool"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "qup_export_dir", text="Thư mục xuất")
        row = layout.row(align=True)
        row.prop(scene, "qup_format", text="Định dạng")
        row.prop(scene, "qup_only_selected", text="Chỉ material chọn")
        layout.operator("qup.export_packed_maps", icon="EXPORT")
        layout.separator()
        layout.label(text="Tên file: Material_MapType.ext")
        layout.label(text="Map types: BaseColor, Roughness, Metallic, Normal, Alpha, Misc")
        layout.separator()
        layout.label(text="Ghi chú: nếu đường dẫn mạng không truy cập, sẽ fallback sang thư mục local.")

# ---------- Register ----------
classes = (
    QUP_OT_export_packed_maps,
    QUP_PT_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.qup_export_dir = bpy.props.StringProperty(
        name="Export Directory",
        subtype='DIR_PATH',
        default="//qup_export"
    )
    bpy.types.Scene.qup_format = bpy.props.EnumProperty(
        name="Format",
        items=[('PNG','PNG',''), ('JPEG','JPEG',''), ('TARGA','TGA','')],
        default='PNG'
    )
    bpy.types.Scene.qup_only_selected = bpy.props.BoolProperty(
        name="Only Selected Material",
        description="Nếu bật: chỉ xuất material của object đang chọn (active + slots)",
        default=False
    )

def unregister():
    # delete props first
    for prop in ("qup_export_dir", "qup_format", "qup_only_selected"):
        if hasattr(bpy.types.Scene, prop):
            try:
                delattr(bpy.types.Scene, prop)
            except Exception:
                try:
                    del bpy.types.Scene.__dict__[prop]
                except Exception:
                    pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

if __name__ == "__main__":
    register()
