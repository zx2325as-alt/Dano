"""Skill catalog package extensions."""

from dano.catalog.option_query_manifest_p1 import install_option_query_manifest_p1
from dano.catalog.option_reference_manifest_p3 import install_option_reference_manifest_p3

# Query capabilities are projected first. P3 then removes recorded raw values and changes
# newly compiled dynamic fields to the opaque-reference caller contract.
install_option_query_manifest_p1()
install_option_reference_manifest_p3()
