#!/usr/bin/env python3
"""Build a scoped OIDC Source harness without editing the production Root.

Terraform 1.16.4 cannot supply six distinct values for RHCS's computed
ListNestedAttribute. Omit that query and its postcondition only in this harness,
and substitute the one local-map input with a typed six-role fixture. Keep every
OIDC/Role/Attachment resource and issuer normalization block byte-for-byte.
The workflow validates the entire original Root separately.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re


FIXTURE = '''# Test-only input. This is not an accepted foundation/Data Source query.
variable "operator_roles_fixture" {
  type = list(object({
    role_name          = string
    policy_name        = string
    operator_namespace = string
    operator_name      = string
    service_accounts   = list(string)
  }))
}

# The actual production input contract and its foundation lookups are outside
# this scoped harness; the original Root keeps them and is validated separately.
resource "terraform_data" "input_contract" {
  input = "MOCK_ONLY_NOT_AN_OWNER_HANDOFF"
}
'''


def digest(value):
    return hashlib.sha256(value).hexdigest()


def block_end(text, opening):
    """Find this HCL brace's end, ignoring strings and comments; fail closed."""
    depth = 0
    state = "code"
    escaped = False
    index = opening
    while index < len(text):
        char = text[index]
        pair = text[index:index + 2]
        if state == "string":
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                state = "code"
        elif state == "line_comment":
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if pair == "*/":
                state = "code"
                index += 1
        elif char == '"':
            state = "string"
        elif char == "#" or pair == "//":
            state = "line_comment"
        elif pair == "/*":
            state = "block_comment"
            index += 1
        elif pair == "<<":
            raise ValueError("Unexpected heredoc in OIDC Source; inspect the harness builder")
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ValueError("Unclosed HCL block in OIDC Source")


def declared_blocks(text):
    result = {}
    pattern = r'(?m)^(resource|data)\s+"([^"]+)"\s+"([^"]+)"\s*\{'
    for match in re.finditer(pattern, text):
        key = tuple(match.group(1, 2, 3))
        if key in result:
            raise ValueError("Duplicate declaration: " + repr(key))
        result[key] = (match.start(), block_end(text, match.end() - 1))
    return result


def build(source_root, output_root):
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if output_root == source_root or source_root in output_root.parents:
        raise ValueError("Harness must be outside the production Root")
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError("Harness directory must be empty")
    source_bytes = (source_root / "oidc.tf").read_bytes()
    source = source_bytes.decode("utf-8")
    blocks = declared_blocks(source)
    query = ("data", "rhcs_rosa_operator_roles", "cluster")
    retained = {
        ("resource", "rhcs_rosa_oidc_config", "cluster"),
        ("resource", "aws_iam_openid_connect_provider", "cluster"),
        ("resource", "aws_iam_role", "operator"),
        ("resource", "aws_iam_role_policy_attachment", "operator"),
    }
    if set(blocks) != retained | {query}:
        raise ValueError("OIDC declaration set changed; inspect the harness scope")
    reference = "data.rhcs_rosa_operator_roles.cluster.operator_iam_roles"
    if source.count(reference) != 1:
        raise ValueError("Expected exactly one Operator local-map input reference")
    start, end = blocks[query]
    omitted = source[start:end]
    harness = source[:start] + source[end:]
    harness = harness.replace(reference, "var.operator_roles_fixture", 1)
    harness_blocks = declared_blocks(harness)
    resource_hashes = {}
    for key in sorted(retained):
        original = source[slice(*blocks[key])]
        copied = harness[slice(*harness_blocks[key])]
        if original != copied:
            raise ValueError("Production resource changed in harness: " + repr(key))
        resource_hashes[".".join(key)] = digest(original.encode("utf-8"))
    locals_match = re.search(r"(?m)^locals\s*\{", source)
    if locals_match is None:
        raise ValueError("Missing issuer normalization locals")
    normalization = source[locals_match.start():block_end(source, locals_match.end() - 1)]
    if harness.count(normalization) != 1:
        raise ValueError("Issuer normalization block was not preserved exactly")
    output_root.mkdir(parents=True, exist_ok=True)
    copy_hashes = {}
    for name in ("versions.tf", "providers.tf", "variables.tf", ".terraform.lock.hcl"):
        content = (source_root / name).read_bytes()
        (output_root / name).write_bytes(content)
        if (output_root / name).read_bytes() != content:
            raise ValueError("Exact copy failed: " + name)
        copy_hashes[name] = digest(content)
    (output_root / "oidc.tf").write_text(harness, encoding="utf-8")
    (output_root / "test_fixture.tf").write_text(FIXTURE, encoding="utf-8")
    (output_root / "tests").mkdir()
    for name in ("oidc_issuer.tftest.hcl", "oidc_mock.tfvars.json"):
        content = (source_root / "tests" / name).read_bytes()
        (output_root / "tests" / name).write_bytes(content)
    if (source_root / "oidc.tf").read_bytes() != source_bytes:
        raise ValueError("Production OIDC Source changed while building harness")
    manifest = {
        "scope": "scoped_source_harness_not_full_root_or_actual_query_acceptance",
        "production_oidc_sha256": digest(source_bytes),
        "harness_oidc_sha256": digest(harness.encode("utf-8")),
        "normalization_sha256": digest(normalization.encode("utf-8")),
        "retained_resource_sha256": resource_hashes,
        "exact_copy_sha256": copy_hashes,
        "omitted_query_sha256": digest(omitted.encode("utf-8")),
        "local_map_input_substitutions": 1,
        "fixture_input_contract": "test_only_builtin_stub",
    }
    (output_root / "source-harness-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    build(args.source_root, args.output_root)
