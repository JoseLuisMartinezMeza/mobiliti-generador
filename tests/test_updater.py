"""
Tests unitarios para el auto-updater (mobiliti_saas/cliente/updater.py).
Prueba logica pura sin dependencias de red ni GUI.
"""

import sys
import os

# Agregamos el path del cliente al sys.path para importar updater
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mobiliti_saas", "cliente"))

import updater


class TestVersionParsing:
    def test_parse_simple(self):
        assert updater._parse_version("1.5.4") == (1, 5, 4)

    def test_parse_with_v_prefix(self):
        assert updater._parse_version("v1.5.4") == (1, 5, 4)

    def test_parse_two_components(self):
        assert updater._parse_version("2.0") == (2, 0, 0)


class TestVersionComparison:
    def test_equal(self):
        assert updater._compare_version("1.5.4", "1.5.4") == 0

    def test_newer_available(self):
        assert updater._compare_version("1.5.3", "1.5.4") == -1

    def test_older_available(self):
        assert updater._compare_version("1.5.4", "1.5.3") == 1

    def test_major_bump(self):
        assert updater._compare_version("1.9.9", "2.0.0") == -1

    def test_different_length(self):
        assert updater._compare_version("1.5", "1.5.0") == 0
