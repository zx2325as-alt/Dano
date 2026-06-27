"""Skill catalog package extensions."""

from dano.catalog.option_query_manifest_p1 import install_option_query_manifest_p1

# Install during package import so every manifest/function-tool consumer receives the
# same business-facing option-query capability metadata.
install_option_query_manifest_p1()
