#!/usr/bin/env python3
"""standard_parser — Parse standard HPROF format (after hprof-conv).

Extracts STRING_DUMP and LOAD_CLASS records to build complete class name mappings.
Also parses HEAP_DUMP sub-records for object instances, arrays, and GC roots.
"""

import struct
import os
import subprocess
from typing import Dict, List, Tuple, Optional, Any


def is_hprof_libs(filepath: str) -> bool:
    """Check if file is Android hprof-libs format."""
    with open(filepath, 'rb') as f:
        f.read(16)  # skip magic
        stated_size = struct.unpack_from('<I', f.read(4), 0)[0]
    return stated_size > 2000


def find_hprof_conv() -> Optional[str]:
    """Find hprof-conv binary."""
    paths = [
        os.path.expanduser('~/Library/Android/sdk/platform-tools/hprof-conv'),
        os.path.expanduser('$ANDROID_HOME/platform-tools/hprof-conv'),
    ]
    for p in paths:
        p = os.path.expandvars(p)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    # Search common paths
    import glob
    for pattern in ['~/Library/Android/**/hprof-conv', '~/Android/**/hprof-conv']:
        for f in glob.glob(os.path.expanduser(pattern), recursive=True):
            if os.access(f, os.X_OK):
                return f
    return None


def convert_to_standard(hprof_path: str, output_path: str) -> bool:
    """Convert Android hprof-libs to standard HPROF using hprof-conv."""
    conv = find_hprof_conv()
    if not conv:
        print("⚠️  hprof-conv not found, skipping standard format conversion")
        return False
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    result = subprocess.run(
        [conv, hprof_path, output_path],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print(f"⚠️  hprof-conv failed: {result.stderr}")
        return False
    return os.path.isfile(output_path)


class StandardHprofParser:
    """Parse standard HPROF format to extract class names, objects, and references."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.file_size = os.path.getsize(filepath)
        self.id_size = 4  # Default; will be read from header
        self.strings: Dict[int, str] = {}  # string_id → text
        self.class_names: Dict[int, str] = {}  # class_serial → class_name
        self.class_obj_ids: Dict[int, int] = {}  # class_serial → class_obj_id
        self.objects: List[Dict] = []  # instance objects
        self.gc_roots: List[Dict] = []
        self.object_arrays: List[Dict] = []
        self.primitive_arrays: List[Dict] = []
        self.static_fields: List[Dict] = []
        self.class_instance_fields = {}  # class_obj_id → list of (field_name_id, field_type)
        self.object_refs = []  # (obj_id, ref_obj_id) pairs
        self.record_start = 0

    def _read_header(self) -> int:
        """Read HPROF header, return record start offset."""
        with open(self.filepath, 'rb') as f:
            # Magic: null-terminated string
            magic = b''
            while True:
                b = f.read(1)
                if b == b'\x00':
                    break
                magic += b
            # Identifier size (u4 BE)
            self.id_size = struct.unpack('>I', f.read(4))[0]
            # Timestamp (u8 BE)
            f.read(8)
            return f.tell()

    def parse_strings_and_classes(self) -> Dict[str, int]:
        """Parse STRING_DUMP and LOAD_CLASS records.
        
        STRING_DUMP (0x01): id(id_size BE) + text(remaining bytes)
        LOAD_CLASS (0x02): serial(4B BE) + obj_id(id_size BE) + stack_trace(4B BE)
                          + name_string_id(id_size BE) + super_obj_id(id_size BE)
                          + loader_obj_id(id_size BE) + signers_obj_id(id_size BE)
                          + prot_domain_obj_id(id_size BE)
        """
        self.record_start = self._read_header()
        counts = {'strings': 0, 'classes': 0}
        
        with open(self.filepath, 'rb') as f:
            f.seek(self.record_start)
            pos = self.record_start
            
            while pos < self.file_size - 9:
                f.seek(pos)
                tag_byte = f.read(1)
                if not tag_byte:
                    break
                tag = tag_byte[0]
                ts = struct.unpack('>I', f.read(4))[0]
                length = struct.unpack('>I', f.read(4))[0]
                
                if length == 0 or pos + 9 + length > self.file_size:
                    break
                
                body_start = f.tell()
                
                if tag == 0x01:  # STRING_DUMP
                    body = f.read(length)
                    if len(body) >= self.id_size:
                        string_id = int.from_bytes(body[:self.id_size], 'big')
                        text = body[self.id_size:].decode('utf-8', errors='replace')
                        self.strings[string_id] = text
                        counts['strings'] += 1
                
                elif tag == 0x02:  # LOAD_CLASS
                    body = f.read(length)
                    # Format: serial(4B) + obj_id(id_size) + stack_trace(4B) + name_string_id(id_size) + ...
                    offset_name = 4 + self.id_size + 4  # serial(4) + obj_id(id_size) + stack_trace(4)
                    if len(body) >= offset_name + self.id_size:
                        serial = struct.unpack_from('>I', body, 0)[0]
                        obj_id = int.from_bytes(body[4:4+self.id_size], 'big')
                        name_string_id = int.from_bytes(
                            body[offset_name:offset_name+self.id_size], 'big')
                        # Lookup class name from strings
                        class_name = self.strings.get(name_string_id, f'class_{serial}')
                        self.class_names[serial] = class_name
                        self.class_obj_ids[serial] = obj_id
                        counts['classes'] += 1
                
                else:
                    # Skip other records for now
                    pass
                
                pos = body_start + length
        
        return counts

    def parse_heap_dump(self) -> int:
        """Parse HEAP_DUMP record and its sub-records."""
        count = 0
        id_size = self.id_size
        
        with open(self.filepath, 'rb') as f:
            f.seek(self.record_start)
            pos = self.record_start
            
            while pos < self.file_size - 9:
                f.seek(pos)
                tag_byte = f.read(1)
                if not tag_byte:
                    break
                tag = tag_byte[0]
                ts = struct.unpack('>I', f.read(4))[0]
                length = struct.unpack('>I', f.read(4))[0]
                
                if length == 0 or pos + 9 + length > self.file_size:
                    break
                
                if tag in (0x0E, 0x2C, 0x0C):  # HEAP_DUMP (0x0C used by hprof-conv)
                    # 注意: payload 开头可能有 13 字节 header, 但子记录可能从不同位置开始
                    # 尝试从 offset 0 解析, 如果失败则从 offset 13 解析
                    payload = f.read(length)
                    # 尝试从 offset 0 解析, 失败则从 offset 13
                    count += self._parse_heap_subrecords(payload)
                else:
                    f.seek(pos + 9 + length)  # skip
                
                pos = pos + 9 + length
        
        return count

    def _parse_heap_subrecords(self, payload: bytes) -> int:
        """Parse HEAP_DUMP sub-records using jvm-hprof tag mapping.
        
        Tag mapping (from jvm-hprof-rs-li-hackweek source):
        0xFF = GcRootUnknown (NOT HEAP_DUMP_END!)
        0x08 = GcRootThreadObj
        0x01 = GcRootJniGlobal
        0x02 = GcRootJniLocalRef
        0x03 = GcRootJavaStackFrame
        0x04 = GcRootNativeStack
        0x05 = GcRootSystemClass
        0x06 = GcRootThreadBlock
        0x07 = GcRootBusyMonitor
        0x20 = Class (CLASS_DUMP)
        0x21 = Instance (INSTANCE_DUMP)
        0x22 = ObjectArray
        0x23 = PrimitiveArray
        """
        import struct as _struct
        count = 0
        id_size = self.id_size
        pos = 0
        payload_len = len(payload)
        
        # type code -> size mapping for field values
        type_sizes = {2: id_size, 4: 1, 5: 2, 6: 4, 7: 8, 8: 1, 9: 2, 10: 4, 11: 8}
        prim_sizes = {4: 1, 5: 2, 6: 4, 7: 8, 8: 1, 9: 2, 10: 4, 11: 8}
        
        while pos < payload_len:
            if pos >= payload_len:
                break
            tag = payload[pos]
            pos += 1
            
            try:
                if tag == 0xFF:  # GcRootUnknown — NOT HEAP_DUMP_END!
                    if pos + id_size > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    self.gc_roots.append({'kind': 'UNKNOWN', 'object_id': obj_id})
                    count += 1
                
                elif tag == 0x08:  # GcRootThreadObj
                    if pos + id_size + 8 > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    pos += 8  # thread_serial + stack_trace_serial
                    self.gc_roots.append({'kind': 'THREAD_OBJ', 'object_id': obj_id})
                    count += 1
                
                elif tag == 0x01:  # GcRootJniGlobal
                    if pos + id_size * 2 > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    pos += id_size  # ref_id
                    self.gc_roots.append({'kind': 'JNI_GLOBAL', 'object_id': obj_id})
                    count += 1
                
                elif tag == 0x02:  # GcRootJniLocalRef
                    if pos + id_size + 8 > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    pos += 8  # thread_serial + frame_index
                    self.gc_roots.append({'kind': 'JNI_LOCAL', 'object_id': obj_id})
                    count += 1
                
                elif tag == 0x03:  # GcRootJavaStackFrame
                    if pos + id_size + 8 > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    pos += 8  # thread_serial + frame_index
                    self.gc_roots.append({'kind': 'JAVA_STACK', 'object_id': obj_id})
                    count += 1
                
                elif tag == 0x04:  # GcRootNativeStack
                    if pos + id_size + 4 > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    pos += 4  # thread_serial
                    self.gc_roots.append({'kind': 'NATIVE_STACK', 'object_id': obj_id})
                    count += 1
                
                elif tag == 0x05:  # GcRootSystemClass
                    if pos + id_size > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    self.gc_roots.append({'kind': 'SYSTEM_CLASS', 'object_id': obj_id})
                    count += 1
                
                elif tag == 0x06:  # GcRootThreadBlock
                    if pos + id_size + 4 > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    pos += 4  # thread_serial
                    self.gc_roots.append({'kind': 'THREAD_BLOCK', 'object_id': obj_id})
                    count += 1
                
                elif tag == 0x07:  # GcRootBusyMonitor
                    if pos + id_size > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    self.gc_roots.append({'kind': 'BUSY_MONITOR', 'object_id': obj_id})
                    count += 1
                
                elif tag == 0x20:  # Class (CLASS_DUMP)
                    if pos + id_size + 4 + id_size * 6 + 4 > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    pos += 4  # stack_trace_serial
                    pos += id_size * 6  # super + loader + signers + prot + reserved1 + reserved2
                    pos += 4  # instance_size
                    
                    # Constant pool — source confirms always 0 (assert_eq!(0, constant_pool_len))
                    if pos + 2 > payload_len: break
                    cp_count = _struct.unpack_from('>H', payload, pos)[0]; pos += 2
                    # assert cp_count == 0  # jvm-hprof source says always 0
                    
                    # Static fields
                    if pos + 2 > payload_len: break
                    sf_count = _struct.unpack_from('>H', payload, pos)[0]; pos += 2
                    for _ in range(sf_count):
                        if pos + id_size + 1 > payload_len: break
                        sf_name_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                        sf_type = payload[pos]; pos += 1
                        sf_val, pos = self._read_value(payload, pos, sf_type, id_size)
                        for serial, coid in self.class_obj_ids.items():
                            if coid == obj_id:
                                self.static_fields.append({
                                    'class_serial': serial,
                                    'class_name': self.class_names.get(serial, ''),
                                    'field_name': self.strings.get(sf_name_id, ''),
                                    'field_type': sf_type,
                                    'ref_id': sf_val if isinstance(sf_val, int) else 0,
                                })
                                break
                    
                    # Instance fields
                    if pos + 2 > payload_len: break
                    if_count = _struct.unpack_from('>H', payload, pos)[0]; pos += 2
                    # Store instance field descriptors for reference parsing
                    if_descriptors = []
                    for _ in range(if_count):
                        if pos + id_size + 1 > payload_len: break
                        fid = int.from_bytes(payload[pos:pos+id_size], 'big')
                        ft = payload[pos + id_size]
                        if_descriptors.append((fid, ft))
                        pos += id_size + 1
                    self.class_instance_fields[obj_id] = if_descriptors
                    count += 1
                
                elif tag == 0x21:  # Instance (INSTANCE_DUMP)
                    if pos + id_size + 4 + id_size + 4 > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    stack_trace = _struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    class_obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    num_bytes = _struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    
                    if pos + num_bytes > payload_len: break
                    field_data = payload[pos:pos+num_bytes]; pos += num_bytes
                    
                    # P1: Parse field data to extract object references
                    if_descriptors = self.class_instance_fields.get(class_obj_id)
                    if if_descriptors and num_bytes > 0:
                        fp = 0
                        for (fid, ft) in if_descriptors:
                            fs = type_sizes.get(ft, id_size)
                            if fp + fs > num_bytes: break
                            if ft == 2:  # object reference
                                ref_id = int.from_bytes(field_data[fp:fp+id_size], 'big')
                                if ref_id > 0:
                                    self.object_refs.append({
                                        'obj_id': obj_id,
                                        'ref_obj_id': ref_id,
                                    })
                            fp += fs
                    
                    class_serial = 0
                    class_name = ''
                    for serial, coid in self.class_obj_ids.items():
                        if coid == class_obj_id:
                            class_serial = serial
                            class_name = self.class_names.get(serial, '')
                            break
                    self.objects.append({
                        'obj_id': obj_id,
                        'class_serial': class_serial,
                        'class_name': class_name,
                        'class_obj_id': class_obj_id,
                        'field_data_size': num_bytes,
                    })
                    count += 1
                
                elif tag == 0x22:  # ObjectArray
                    # Source order: obj_id, stack_trace_serial, num_elements, array_class_id, elements
                    if pos + id_size + 4 + 4 + id_size > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    pos += 4  # stack_trace_serial
                    num_elements = _struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    array_class_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    
                    elements = []
                    for _ in range(num_elements):
                        if pos + id_size > payload_len: break
                        elem = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                        elements.append(elem)
                    self.object_arrays.append({
                        'obj_id': obj_id,
                        'class_name': '[object',
                        'elements': elements,
                    })
                    count += 1
                
                elif tag == 0x23:  # PrimitiveArray
                    if pos + id_size + 4 + 4 + 1 > payload_len: break
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    pos += 4  # stack_trace_serial
                    num_elements = _struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    elem_type = payload[pos]; pos += 1
                    
                    elem_size = prim_sizes.get(elem_type, 4)
                    data_size = num_elements * elem_size
                    if pos + data_size > payload_len: break
                    pos += data_size
                    self.primitive_arrays.append({
                        'obj_id': obj_id,
                        'type': elem_type,
                        'count': num_elements,
                    })
                    count += 1
                
                else:
                    # Unknown tag: skip 1 byte and continue
                    continue
                    
            except (IndexError, _struct.error):
                continue
        
        return count

    def _type_size(self, type_code: int, id_size: int) -> int:
        """Get size of a field type.
        HPROF: 2=object, 4=boolean(1), 5=char(2), 6=float(4), 7=double(8),
        8=byte(1), 9=short(2), 10=int(4), 11=long(8)
        """
        sizes = {2: id_size, 4: 1, 5: 2, 6: 4, 7: 8, 8: 1, 9: 2, 10: 4, 11: 8}
        return sizes.get(type_code, id_size)

    def _read_value(self, payload: bytes, pos: int, type_code: int, id_size: int) -> Tuple[Any, int]:
        """Read a field value based on type code.
        HPROF type codes: 2=object, 4=boolean, 5=char, 6=float, 7=double,
        8=byte, 9=short, 10=int, 11=long
        """
        sizes = {2: id_size, 4: 1, 5: 2, 6: 4, 7: 8, 8: 1, 9: 2, 10: 4, 11: 8}
        size = sizes.get(type_code, id_size)
        if pos + size > len(payload):
            return 0, pos + id_size
        val = int.from_bytes(payload[pos:pos+size], 'big')
        return val, pos + size

    def _prim_array_elem_size(self, elem_type: int) -> int:
        """Get element size for primitive array type."""
        return {4: 1, 5: 2, 6: 2, 7: 4, 8: 4, 9: 8, 10: 8}.get(elem_type, 4)

    def parse_all(self) -> Dict[str, Any]:
        """Parse all records and return aggregated data."""
        counts = self.parse_strings_and_classes()
        heap_count = self.parse_heap_dump()
        return {
            'strings': self.strings,
            'class_names': self.class_names,
            'class_obj_ids': self.class_obj_ids,
            'objects': self.objects,
            'gc_roots': self.gc_roots,
            'object_arrays': self.object_arrays,
            'primitive_arrays': self.primitive_arrays,
            'static_fields': self.static_fields,
            'stats': {
                'strings': counts['strings'],
                'classes': counts['classes'],
                'heap_records': heap_count,
                'objects': len(self.objects),
                'gc_roots': len(self.gc_roots),
                'object_arrays': len(self.object_arrays),
                'primitive_arrays': len(self.primitive_arrays),
                'static_fields': len(self.static_fields),
            }
        }
