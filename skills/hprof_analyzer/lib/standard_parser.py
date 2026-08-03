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
                    payload = f.read(length)
                    count += self._parse_heap_subrecords(payload)
                else:
                    f.seek(pos + 9 + length)  # skip
                
                pos = pos + 9 + length
        
        return count

    def _parse_heap_subrecords(self, payload: bytes) -> int:
        """Parse HEAP_DUMP sub-records."""
        count = 0
        id_size = self.id_size
        pos = 0
        payload_len = len(payload)
        
        while pos < payload_len:
            if pos >= payload_len:
                break
            sub_tag = payload[pos]
            pos += 1
            
            try:
                if sub_tag == 0x01:  # ROOT_JNI_GLOBAL
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    ref = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    self.gc_roots.append({'kind': 'JNI_GLOBAL', 'object_id': obj_id})
                    count += 1
                elif sub_tag == 0x02:  # ROOT_JNI_LOCAL
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    thread_serial, frame = struct.unpack_from('>II', payload, pos); pos += 8
                    self.gc_roots.append({'kind': 'JNI_LOCAL', 'object_id': obj_id})
                    count += 1
                elif sub_tag == 0x03:  # ROOT_JAVA_FRAME
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    thread_serial, frame = struct.unpack_from('>II', payload, pos); pos += 8
                    self.gc_roots.append({'kind': 'JAVA_FRAME', 'object_id': obj_id})
                    count += 1
                elif sub_tag == 0x04:  # ROOT_NATIVE_STACK
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    thread_serial = struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    self.gc_roots.append({'kind': 'NATIVE_STACK', 'object_id': obj_id})
                    count += 1
                elif sub_tag == 0x05:  # ROOT_MONITOR_USED
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    self.gc_roots.append({'kind': 'MONITOR_USED', 'object_id': obj_id})
                    count += 1
                elif sub_tag == 0x06:  # ROOT_THREAD_OBJ
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    thread_serial, stack_serial = struct.unpack_from('>II', payload, pos); pos += 8
                    self.gc_roots.append({'kind': 'THREAD_OBJ', 'object_id': obj_id})
                    count += 1
                elif sub_tag == 0x20:  # CLASS_DUMP
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    pos += 4 + id_size * 6 + 4  # stack_trace + super+loader+signers+prot+reserved1+reserved2 + instance_size
                    # Constant pool
                    cp_count = struct.unpack_from('>H', payload, pos)[0] if pos+2 <= payload_len else 0; pos += 2
                    for _ in range(cp_count):
                        pos += 2; t = payload[pos-1]
                        pos += self._type_size(t, id_size)
                    # Static fields
                    sf_count = struct.unpack_from('>H', payload, pos)[0] if pos+2 <= payload_len else 0; pos += 2
                    for _ in range(sf_count):
                        sf_name_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                        sf_type = payload[pos]; pos += 1
                        sf_value, pos = self._read_value(payload, pos, sf_type, id_size)
                        # Find class name for this class_obj_id
                        for serial, coid in self.class_obj_ids.items():
                            if coid == obj_id:
                                self.static_fields.append({
                                    'class_serial': serial,
                                    'class_name': self.class_names.get(serial, ''),
                                    'field_name': self.strings.get(sf_name_id, ''),
                                    'field_type': sf_type,
                                    'ref_id': sf_value if isinstance(sf_value, int) else 0,
                                })
                                break
                    # Instance fields
                    if_count = struct.unpack_from('>H', payload, pos)[0] if pos+2 <= payload_len else 0; pos += 2
                    for _ in range(if_count):
                        pos += id_size + 1
                elif sub_tag == 0x21:  # ROOT_STICKY_CLASS
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    self.gc_roots.append({'kind': 'STICKY_CLASS', 'object_id': obj_id})
                    count += 1
                elif sub_tag == 0x22:  # ROOT_THREAD_BLOCK
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    thread_serial = struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    self.gc_roots.append({'kind': 'THREAD_BLOCK', 'object_id': obj_id})
                    count += 1
                elif sub_tag == 0x23:  # INSTANCE_DUMP (CLASS_OBJ in some versions)
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    stack_trace = struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    class_obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    remaining = struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    # Read field data
                    field_data = payload[pos:pos+remaining]; pos += remaining
                    # Find class serial from class_obj_id
                    class_serial = 0
                    class_name = ''
                    for serial, coid in self.class_obj_ids.items():
                        if coid == class_obj_id:
                            class_serial = serial
                            class_name = self.class_names.get(serial, f'class_{serial}')
                            break
                    self.objects.append({
                        'obj_id': obj_id,
                        'class_serial': class_serial,
                        'class_name': class_name,
                        'field_data_size': remaining,
                    })
                    count += 1
                elif sub_tag == 0x24:  # INSTANCE_DUMP (alternative tag)
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    stack_trace = struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    class_obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    remaining = struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    field_data = payload[pos:pos+remaining]; pos += remaining
                    class_serial = 0
                    class_name = ''
                    for serial, coid in self.class_obj_ids.items():
                        if coid == class_obj_id:
                            class_serial = serial
                            class_name = self.class_names.get(serial, f'class_{serial}')
                            break
                    self.objects.append({
                        'obj_id': obj_id,
                        'class_serial': class_serial,
                        'class_name': class_name,
                        'field_data_size': remaining,
                    })
                    count += 1
                elif sub_tag == 0x25:  # OBJECT_ARRAY
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    stack_trace = struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    array_class_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    elem_count = struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    elements = []
                    for _ in range(elem_count):
                        if pos + id_size <= payload_len:
                            elem = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                            elements.append(elem)
                    self.object_arrays.append({
                        'obj_id': obj_id,
                        'class_name': '[object',
                        'elements': elements,
                    })
                    count += 1
                elif sub_tag == 0x26:  # PRIMITIVE_ARRAY
                    obj_id = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
                    stack_trace = struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    elem_type = payload[pos]; pos += 1
                    elem_count = struct.unpack_from('>I', payload, pos)[0]; pos += 4
                    elem_size = self._prim_array_elem_size(elem_type)
                    pos += elem_count * elem_size
                    self.primitive_arrays.append({
                        'obj_id': obj_id,
                        'type': elem_type,
                        'count': elem_count,
                    })
                    count += 1
                elif sub_tag == 0xFF:  # HEAP_DUMP_END
                    break
                else:
                    # Unknown sub-record, try to resync
                    break
            except (IndexError, struct.error):
                break
        
        return count

    def _type_size(self, type_code: int, id_size: int) -> int:
        """Get size of a field type."""
        sizes = {2: id_size, 4: 1, 5: 2, 6: 2, 7: 4, 8: 4, 9: 8, 10: 8}
        return sizes.get(type_code, id_size)

    def _read_value(self, payload: bytes, pos: int, type_code: int, id_size: int) -> Tuple[Any, int]:
        """Read a field value based on type code."""
        if type_code == 2:  # object
            val = int.from_bytes(payload[pos:pos+id_size], 'big'); pos += id_size
            return val, pos
        elif type_code in (4,):  # boolean/byte
            return payload[pos], pos + 1
        elif type_code in (5, 6):  # char/short
            return struct.unpack_from('>h', payload, pos)[0], pos + 2
        elif type_code in (7, 8):  # int/float
            return struct.unpack_from('>I', payload, pos)[0], pos + 4
        elif type_code in (9, 10):  # long/double
            return struct.unpack_from('>Q', payload, pos)[0], pos + 8
        return 0, pos + id_size

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
