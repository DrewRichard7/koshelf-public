"""KoShelf only handles DRM-free books. These tests keep that boundary in place."""
from pathlib import Path
import tempfile
import unittest
import zipfile

import kindle_delivery
from test_app import epub

ADOBE_DRM = '''<?xml version="1.0"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
<enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>
<enc:CipherData><enc:CipherReference URI="OPS/chapter.xhtml"/></enc:CipherData></enc:EncryptedData></encryption>'''

FONT_OBFUSCATION = ADOBE_DRM.replace('http://www.w3.org/2001/04/xmlenc#aes128-cbc', 'http://www.idpf.org/2008/embedding') \
    .replace('OPS/chapter.xhtml', 'OPS/font.otf')


class DrmBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'book.epub'
        epub(self.path)

    def add(self, name, data):
        with zipfile.ZipFile(self.path, 'a') as archive:
            archive.writestr(name, data)

    def test_rights_management_file_is_refused(self):
        self.add('META-INF/rights.xml', '<rights/>')
        with self.assertRaisesRegex(ValueError, 'protected'):
            kindle_delivery.check_epub(self.path)

    def test_encrypted_chapters_are_refused(self):
        self.add('META-INF/encryption.xml', ADOBE_DRM)
        with self.assertRaisesRegex(ValueError, 'protected'):
            kindle_delivery.check_epub(self.path)

    def test_zip_level_encryption_is_refused(self):
        self.add('OPS/secret.xhtml', b'ciphertext')
        # zipfile never writes the encryption flag, so set it in both headers directly.
        data = bytearray(self.path.read_bytes())
        name = b'OPS/secret.xhtml'
        for signature, flag_offset, name_offset in ((b'PK\x03\x04', 6, 30), (b'PK\x01\x02', 8, 46)):
            start = next(index for index in range(len(data)) if data.startswith(signature, index)
                         and data[index + name_offset:index + name_offset + len(name)] == name)
            data[start + flag_offset] |= 1
        self.path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'protected'):
            kindle_delivery.check_epub(self.path)

    def test_standard_font_obfuscation_is_not_drm(self):
        self.add('META-INF/encryption.xml', FONT_OBFUSCATION)
        kindle_delivery.check_encryption(kindle_delivery.parse_xml(FONT_OBFUSCATION.encode()))


if __name__ == '__main__':
    unittest.main()
