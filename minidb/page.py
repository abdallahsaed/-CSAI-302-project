"""Slotted-page layout used by the ROW store.

    +-----------+----------------------+--------------- free ---------------+-------------+
    | header 10B| slot dir (4B/slot) ->|                                    |<- records   |
    +-----------+----------------------+------------------------------------+-------------+

header = type(1) has_dead(1) num_slots(2) free_end(2) next_page(4)
slot   = offset(2) length(2);  offset 0 marks a deleted slot (slot ids stay stable,
so a record id (page_id, slot) never changes unless the record is relocated).
All functions operate in place on the frame's bytearray (no copies).
"""
import struct

HEADER_SIZE = 10
SLOT_SIZE = 4
PAGE_TYPE_ROW = 1

_HDR = struct.Struct("<BBHHI")
_SLOT = struct.Struct("<HH")
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")


def init_page(buf, next_page=0):
    _HDR.pack_into(buf, 0, PAGE_TYPE_ROW, 0, 0, len(buf), next_page)


def num_slots(buf):
    return _U16.unpack_from(buf, 2)[0]


def free_end(buf):
    return _U16.unpack_from(buf, 4)[0]


def next_page(buf):
    return _U32.unpack_from(buf, 6)[0]


def set_next_page(buf, pid):
    _U32.pack_into(buf, 6, pid)


def slot_span(buf, slot):
    """(offset, length) of a live record, else None."""
    if slot < 0 or slot >= num_slots(buf):
        return None
    off, length = _SLOT.unpack_from(buf, HEADER_SIZE + slot * SLOT_SIZE)
    return (off, length) if off else None


def _live_bytes(buf, skip=-1):
    total = 0
    for s in range(num_slots(buf)):
        if s == skip:
            continue
        off, length = _SLOT.unpack_from(buf, HEADER_SIZE + s * SLOT_SIZE)
        if off:
            total += length
    return total


def compact(buf):
    """Slide all live records to the end of the page (slot ids unchanged)."""
    recs = []
    for s in range(num_slots(buf)):
        off, length = _SLOT.unpack_from(buf, HEADER_SIZE + s * SLOT_SIZE)
        if off:
            recs.append((s, bytes(buf[off:off + length])))
    end = len(buf)
    for s, r in recs:
        end -= len(r)
        buf[end:end + len(r)] = r
        _SLOT.pack_into(buf, HEADER_SIZE + s * SLOT_SIZE, end, len(r))
    _U16.pack_into(buf, 4, end)


def insert(buf, rec):
    """Insert a record; return its slot, or None if the page cannot hold it."""
    n, fe, length = num_slots(buf), free_end(buf), len(rec)
    slot = None
    if buf[1]:                                    # page has dead slots -> try to reuse one
        for s in range(n):
            if _SLOT.unpack_from(buf, HEADER_SIZE + s * SLOT_SIZE)[0] == 0:
                slot = s
                break
    new_slot = slot is None
    dir_end = HEADER_SIZE + (n + (1 if new_slot else 0)) * SLOT_SIZE
    if fe - dir_end < length:
        if not buf[1]:
            return None
        if dir_end + _live_bytes(buf) + length > len(buf):
            return None
        compact(buf)
        fe = free_end(buf)
    fe -= length
    buf[fe:fe + length] = rec
    if new_slot:
        slot = n
        _U16.pack_into(buf, 2, n + 1)
    _SLOT.pack_into(buf, HEADER_SIZE + slot * SLOT_SIZE, fe, length)
    _U16.pack_into(buf, 4, fe)
    return slot


def delete(buf, slot):
    if slot_span(buf, slot) is None:
        return False
    _SLOT.pack_into(buf, HEADER_SIZE + slot * SLOT_SIZE, 0, 0)
    buf[1] = 1
    return True


def update(buf, slot, rec):
    """True = updated in place on this page, False = no room (caller relocates),
    None = slot is not a live record."""
    span = slot_span(buf, slot)
    if span is None:
        return None
    off, old = span
    length = len(rec)
    if length <= old:
        buf[off:off + length] = rec
        _SLOT.pack_into(buf, HEADER_SIZE + slot * SLOT_SIZE, off, length)
        if length < old:
            buf[1] = 1
        return True
    n = num_slots(buf)
    if HEADER_SIZE + n * SLOT_SIZE + _live_bytes(buf, skip=slot) + length > len(buf):
        return False
    _SLOT.pack_into(buf, HEADER_SIZE + slot * SLOT_SIZE, 0, 0)      # free old copy
    buf[1] = 1
    if free_end(buf) - (HEADER_SIZE + n * SLOT_SIZE) < length:
        compact(buf)
    fe = free_end(buf) - length
    buf[fe:fe + length] = rec
    _SLOT.pack_into(buf, HEADER_SIZE + slot * SLOT_SIZE, fe, length)
    _U16.pack_into(buf, 4, fe)
    return True
