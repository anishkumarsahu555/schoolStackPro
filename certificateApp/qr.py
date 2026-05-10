import base64
import io
import zlib

import qrcode


_GF_EXP = [0] * 512
_GF_LOG = [0] * 256


def _init_gf():
    value = 1
    for index in range(255):
        _GF_EXP[index] = value
        _GF_LOG[value] = index
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for index in range(255, 512):
        _GF_EXP[index] = _GF_EXP[index - 255]


_init_gf()


def _gf_mul(left, right):
    if not left or not right:
        return 0
    return _GF_EXP[_GF_LOG[left] + _GF_LOG[right]]


def _rs_generator(degree):
    coeffs = [1]
    for index in range(degree):
        coeffs.append(0)
        alpha = _GF_EXP[index]
        for pos in range(len(coeffs) - 1):
            coeffs[pos] = coeffs[pos] ^ _gf_mul(coeffs[pos + 1], alpha)
    return coeffs


def _rs_remainder(data, degree):
    generator = _rs_generator(degree)
    result = [0] * degree
    for value in data:
        factor = value ^ result.pop(0)
        result.append(0)
        if factor:
            for index in range(degree):
                result[index] ^= _gf_mul(generator[index], factor)
    return result


class _BitBuffer:
    def __init__(self):
        self.bits = []

    def append(self, value, length):
        for shift in range(length - 1, -1, -1):
            self.bits.append((value >> shift) & 1)

    def to_bytes(self):
        rows = []
        for index in range(0, len(self.bits), 8):
            value = 0
            for bit in self.bits[index:index + 8]:
                value = (value << 1) | bit
            rows.append(value)
        return rows


def _build_codewords(payload):
    raw = payload.encode('utf-8')
    if len(raw) > 156:
        raw = raw[:156]

    bits = _BitBuffer()
    bits.append(0b0100, 4)
    bits.append(len(raw), 8)
    for value in raw:
        bits.append(value, 8)

    capacity_bits = 156 * 8
    terminator = min(4, capacity_bits - len(bits.bits))
    if terminator:
        bits.append(0, terminator)
    while len(bits.bits) % 8:
        bits.append(0, 1)

    data = bits.to_bytes()
    pad_values = [0xEC, 0x11]
    pad_index = 0
    while len(data) < 156:
        data.append(pad_values[pad_index % 2])
        pad_index += 1

    blocks = [data[:78], data[78:]]
    ecc_blocks = [_rs_remainder(block, 20) for block in blocks]
    codewords = []
    for index in range(78):
        for block in blocks:
            codewords.append(block[index])
    for index in range(20):
        for block in ecc_blocks:
            codewords.append(block[index])
    return codewords


def _empty_matrix(size):
    return [[False for _ in range(size)] for _ in range(size)], [[False for _ in range(size)] for _ in range(size)]


def _set_module(matrix, reserved, row, col, value, reserve=True):
    if 0 <= row < len(matrix) and 0 <= col < len(matrix):
        matrix[row][col] = bool(value)
        if reserve:
            reserved[row][col] = True


def _draw_finder(matrix, reserved, row, col):
    for y in range(-1, 8):
        for x in range(-1, 8):
            rr, cc = row + y, col + x
            if not (0 <= rr < len(matrix) and 0 <= cc < len(matrix)):
                continue
            is_dark = 0 <= x <= 6 and 0 <= y <= 6 and (x in {0, 6} or y in {0, 6} or (2 <= x <= 4 and 2 <= y <= 4))
            _set_module(matrix, reserved, rr, cc, is_dark)


def _draw_alignment(matrix, reserved, center_row, center_col):
    if reserved[center_row][center_col]:
        return
    for y in range(-2, 3):
        for x in range(-2, 3):
            is_dark = max(abs(x), abs(y)) in {0, 2}
            _set_module(matrix, reserved, center_row + y, center_col + x, is_dark)


def _draw_patterns(matrix, reserved):
    size = len(matrix)
    _draw_finder(matrix, reserved, 0, 0)
    _draw_finder(matrix, reserved, 0, size - 7)
    _draw_finder(matrix, reserved, size - 7, 0)

    for index in range(8, size - 8):
        value = index % 2 == 0
        _set_module(matrix, reserved, 6, index, value)
        _set_module(matrix, reserved, index, 6, value)

    for row in [6, 22, 38]:
        for col in [6, 22, 38]:
            _draw_alignment(matrix, reserved, row, col)

    _set_module(matrix, reserved, size - 8, 8, True)

    for index in range(9):
        if index != 6:
            reserved[8][index] = True
            reserved[index][8] = True
    for index in range(8):
        reserved[8][size - 1 - index] = True
        reserved[size - 1 - index][8] = True

    for index in range(18):
        reserved[index // 3][size - 11 + index % 3] = True
        reserved[size - 11 + index % 3][index // 3] = True


def _bch_remainder(value, polynomial):
    degree = polynomial.bit_length() - 1
    value <<= degree
    while value.bit_length() - 1 >= degree:
        value ^= polynomial << (value.bit_length() - polynomial.bit_length())
    return value


def _draw_format_and_version(matrix, reserved):
    size = len(matrix)
    format_value = ((0b01 << 3) | 0) << 10
    format_bits = (format_value | _bch_remainder((0b01 << 3) | 0, 0x537)) ^ 0x5412
    format_positions_a = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8), (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    format_positions_b = [(size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8), (size - 5, 8), (size - 6, 8), (size - 7, 8), (8, size - 8), (8, size - 7), (8, size - 6), (8, size - 5), (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1)]
    for index in range(15):
        bit = (format_bits >> index) & 1
        _set_module(matrix, reserved, *format_positions_a[index], bit)
        _set_module(matrix, reserved, *format_positions_b[index], bit)

    version_bits = (7 << 12) | _bch_remainder(7, 0x1F25)
    for index in range(18):
        bit = (version_bits >> index) & 1
        _set_module(matrix, reserved, index // 3, size - 11 + index % 3, bit)
        _set_module(matrix, reserved, size - 11 + index % 3, index // 3, bit)


def _mask(row, col):
    return (row + col) % 2 == 0


def _draw_data(matrix, reserved, codewords):
    bits = []
    for value in codewords:
        for shift in range(7, -1, -1):
            bits.append((value >> shift) & 1)

    size = len(matrix)
    bit_index = 0
    row = size - 1
    direction = -1
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        while 0 <= row < size:
            for offset in range(2):
                cc = col - offset
                if reserved[row][cc]:
                    continue
                bit = bits[bit_index] if bit_index < len(bits) else 0
                if _mask(row, cc):
                    bit ^= 1
                _set_module(matrix, reserved, row, cc, bit, reserve=False)
                bit_index += 1
            row += direction
        row -= direction
        direction *= -1
        col -= 2


def qr_svg_data_uri(payload, *, module_size=4, border=4):
    matrix = qr_matrix(payload)
    size = len(matrix)
    full_size = (size + border * 2) * module_size
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {full_size} {full_size}" width="{full_size}" height="{full_size}" shape-rendering="crispEdges">',
        '<rect width="100%" height="100%" fill="#fff"/>',
    ]
    for row_index, row in enumerate(matrix):
        for col_index, value in enumerate(row):
            if value:
                x = (col_index + border) * module_size
                y = (row_index + border) * module_size
                parts.append(f'<rect x="{x}" y="{y}" width="{module_size}" height="{module_size}" fill="#000"/>')
    parts.append('</svg>')
    encoded = base64.b64encode(''.join(parts).encode('utf-8')).decode('ascii')
    return f'data:image/svg+xml;base64,{encoded}'


def qr_png_data_uri(payload, *, module_size=12, border=4):
    matrix = qr_matrix(payload)
    size = len(matrix)
    full_size = (size + border * 2) * module_size
    raw_rows = []
    for y in range(full_size):
        matrix_row = (y // module_size) - border
        row = bytearray([0])
        for x in range(full_size):
            matrix_col = (x // module_size) - border
            dark = 0 <= matrix_row < size and 0 <= matrix_col < size and matrix[matrix_row][matrix_col]
            row.extend((0, 0, 0) if dark else (255, 255, 255))
        raw_rows.append(bytes(row))
    raw = b''.join(raw_rows)

    def chunk(kind, data):
        crc = zlib.crc32(kind + data) & 0xffffffff
        return len(data).to_bytes(4, 'big') + kind + data + crc.to_bytes(4, 'big')

    png = (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', full_size.to_bytes(4, 'big') + full_size.to_bytes(4, 'big') + bytes([8, 2, 0, 0, 0]))
        + chunk(b'IDAT', zlib.compress(raw, 9))
        + chunk(b'IEND', b'')
    )
    encoded = base64.b64encode(png).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def qr_matrix(payload):
    size = 45
    matrix, reserved = _empty_matrix(size)
    _draw_patterns(matrix, reserved)
    _draw_data(matrix, reserved, _build_codewords(payload))
    _draw_format_and_version(matrix, reserved)
    return matrix


def qr_png_data_uri(payload, *, module_size=12, border=4):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=module_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color='black', back_color='white').convert('RGB')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def qr_svg_data_uri(payload, *, module_size=8, border=4):
    return qr_png_data_uri(payload, module_size=module_size, border=border)
