"""Sites, locations, document kinds and metadata fields, loaded from TOML config.

Config lives in ECM_CONFIG_DIR (default: ecm_client/config/):

    document_kinds.toml   kinds + metadata fields, shared by every environment
    sites.<env>.toml      site ids + folder paths for ECM_ENV=<env> (sites.test.toml, ...)

Nothing about a site, path or kind is hard-coded in Python: add a kind, a site or a
path by editing those files. load_catalog() validates them and raises ECMConfigError
listing every problem it finds.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .errors import ECMConfigError, ECMValidationError

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent / "config"
KINDS_FILE = "document_kinds.toml"
FIELD_TYPES = ("string", "date", "boolean", "integer")
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Field:
    key: str
    property: str
    type: str = "string"
    is_list: bool = False

    def convert(self, value: Any) -> Any:
        """Turn a filter/upload value (often a string from a CLI or query string) into its type."""
        if self.is_list and isinstance(value, (list, tuple)):
            return [self._one(v) for v in value]
        return self._one(value)

    def _one(self, value: Any) -> Any:
        if self.type == "boolean":
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in {"1", "true", "yes"}:
                return True
            if text in {"0", "false", "no"}:
                return False
            raise ECMValidationError(f"{self.key} must be true/false, got {value!r}")
        if self.type == "integer":
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ECMValidationError(f"{self.key} must be an integer, got {value!r}") from exc
        if self.type == "date":
            if isinstance(value, datetime):
                return value.date().isoformat()
            if isinstance(value, date):
                return value.isoformat()
            try:
                return date.fromisoformat(str(value).strip()[:10]).isoformat()
            except ValueError as exc:
                raise ECMValidationError(f"{self.key} must be YYYY-MM-DD, got {value!r}") from exc
        return str(value).strip()


@dataclass(frozen=True)
class UploadRule:
    location: str
    required: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentKind:
    code: str
    label: str
    doc_type: str
    locations: tuple[str, ...]
    fields: tuple[str, ...] = ()
    filters: dict[str, Any] = field(default_factory=dict)
    upload: UploadRule | None = None


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    site_id: str | None
    locations: dict[str, str] = field(default_factory=dict)

    def resolve(self, location: str, values: dict[str, Any] | None = None) -> tuple[str, set[str], bool]:
        """Fill a location's {placeholders} from `values`.

        Returns (path, field keys used, complete). When a placeholder has no value the path
        stops just before it, so a search looks under the parent folder instead.
        """
        if location not in self.locations:
            raise ECMValidationError(f"site {self.key!r} has no {location!r} location; "
                                     f"it has: {sorted(self.locations)}")
        template, values = self.locations[location], values or {}
        out, used, pos = [], set(), 0
        for m in _PLACEHOLDER.finditer(template):
            out.append(template[pos:m.start()])
            value = values.get(m.group(1))
            if value in (None, ""):
                return "".join(out).rstrip("/"), used, False
            if "/" in str(value) or str(value) in {".", ".."}:
                raise ECMValidationError(f"{m.group(1)} cannot contain '/' or be '.'/'..': {value!r}")
            out.append(str(value))
            used.add(m.group(1))
            pos = m.end()
        out.append(template[pos:])
        return "".join(out).rstrip("/"), used, True


@dataclass(frozen=True)
class Catalog:
    environment: str
    doc_type: str
    type_property: str
    fields: dict[str, Field]
    kinds: dict[str, DocumentKind]
    sites: dict[str, Site]
    files: tuple[str, ...] = ()

    def site(self, key: str) -> Site:
        k = (key or "").strip().lower()
        if k not in self.sites:
            raise ECMValidationError(f"unknown site {key!r}; known: {sorted(self.sites)}")
        return self.sites[k]

    def kind(self, code: str) -> DocumentKind:
        if code not in self.kinds:
            raise ECMValidationError(f"unknown kind {code!r}; known: {sorted(self.kinds)}")
        return self.kinds[code]

    def field(self, key: str) -> Field:
        if key not in self.fields:
            raise ECMValidationError(f"unknown field {key!r}; known: {sorted(self.fields)}")
        return self.fields[key]

    def as_dict(self) -> dict:
        return {
            "environment": self.environment,
            "doc_type": self.doc_type,
            "type_property": self.type_property,
            "fields": [{"key": f.key, "property": f.property, "type": f.type, "list": f.is_list}
                       for f in self.fields.values()],
            "sites": [{"key": s.key, "name": s.name, "site_id": s.site_id, "locations": dict(s.locations)}
                      for s in self.sites.values()],
            "kinds": [{"code": k.code, "label": k.label, "doc_type": k.doc_type,
                       "locations": list(k.locations), "fields": list(k.fields), "filters": k.filters,
                       "upload": ({"location": k.upload.location, "required": list(k.upload.required)}
                                  if k.upload else None)}
                      for k in self.kinds.values()],
        }


# --------------------------------------------------------------------------- loading
def config_dir(override: str | Path | None = None) -> Path:
    return Path(override or os.environ.get("ECM_CONFIG_DIR") or DEFAULT_CONFIG_DIR).expanduser()


def _read(path: Path) -> dict:
    if not path.is_file():
        raise ECMConfigError(f"config file not found: {path}")
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ECMConfigError(f"{path.name}: {exc}") from exc


def _str_list(value: Any) -> list | None:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    return None


def load_catalog(environment: str, directory: str | Path | None = None) -> Catalog:
    env = environment.strip().upper()
    base = config_dir(directory)
    kinds_path, sites_path = base / KINDS_FILE, base / f"sites.{env.lower()}.toml"
    raw_kinds, raw_sites = _read(kinds_path), _read(sites_path)
    errors: list[str] = []
    err = errors.append

    doc_type = raw_kinds.get("doc_type")
    type_property = raw_kinds.get("type_property")
    if not isinstance(doc_type, str) or not doc_type:
        err(f"{KINDS_FILE}: doc_type must be a non-empty string")
    if not isinstance(type_property, str) or not type_property:
        err(f"{KINDS_FILE}: type_property must be a non-empty string")

    # fields
    fields: dict[str, Field] = {}
    for key, spec in (raw_kinds.get("fields") or {}).items():
        where = f"{KINDS_FILE} [fields].{key}"
        if not _KEY.match(key):
            err(f"{where}: keys must be lower_snake_case")
            continue
        if isinstance(spec, str):
            spec = {"property": spec}
        if not isinstance(spec, dict) or not isinstance(spec.get("property"), str) or not spec["property"]:
            err(f"{where}: needs a property name")
            continue
        unknown = set(spec) - {"property", "type", "list"}
        if unknown:
            err(f"{where}: unknown settings {sorted(unknown)}")
        ftype = spec.get("type", "string")
        if ftype not in FIELD_TYPES:
            err(f"{where}: type must be one of {FIELD_TYPES}, got {ftype!r}")
            continue
        fields[key] = Field(key, spec["property"], ftype, bool(spec.get("list", False)))

    # kinds
    kinds: dict[str, DocumentKind] = {}
    for code, spec in (raw_kinds.get("kinds") or {}).items():
        where = f"{KINDS_FILE} [kinds.{code}]"
        if not isinstance(spec, dict):
            err(f"{where}: must be a table")
            continue
        unknown = set(spec) - {"label", "doc_type", "locations", "fields", "filters", "upload"}
        if unknown:
            err(f"{where}: unknown settings {sorted(unknown)}")
        locations = _str_list(spec.get("locations"))
        if not locations:
            err(f"{where}: locations must be a non-empty list of names")
            locations = []
        kfields = _str_list(spec.get("fields", []))
        if kfields is None:
            err(f"{where}: fields must be a list of field keys")
            kfields = []
        for f in kfields:
            if f not in fields:
                err(f"{where}: field {f!r} is not defined in [fields]")
        filters = spec.get("filters", {})
        if not isinstance(filters, dict):
            err(f"{where}: filters must be a table")
            filters = {}
        for f, v in filters.items():
            if f not in fields:
                err(f"{where}: filter field {f!r} is not defined in [fields]")
            else:
                try:
                    fields[f].convert(v)
                except ECMValidationError as exc:
                    err(f"{where}: filter {exc}")
        upload = None
        if "upload" in spec:
            up = spec["upload"]
            if not isinstance(up, dict) or not isinstance(up.get("location"), str):
                err(f"{where}: upload needs a location")
            else:
                required = _str_list(up.get("required", []))
                if required is None:
                    err(f"{where}: upload.required must be a list of field keys")
                    required = []
                if up["location"] not in locations:
                    err(f"{where}: upload.location {up['location']!r} is not in its locations")
                for f in required:
                    if f not in kfields:
                        err(f"{where}: upload requires {f!r}, which is not in its fields")
                upload = UploadRule(up["location"], tuple(required))
        kinds[code] = DocumentKind(code=code, label=str(spec.get("label") or code),
                                   doc_type=str(spec.get("doc_type") or doc_type),
                                   locations=tuple(locations), fields=tuple(kfields),
                                   filters=dict(filters), upload=upload)

    # sites
    sites: dict[str, Site] = {}
    sname = sites_path.name
    for key, spec in (raw_sites.get("sites") or {}).items():
        where = f"{sname} [sites.{key}]"
        if not _KEY.match(key):
            err(f"{where}: site keys must be lower_snake_case")
            continue
        if not isinstance(spec, dict):
            err(f"{where}: must be a table")
            continue
        unknown = set(spec) - {"name", "site_id", "locations"}
        if unknown:
            err(f"{where}: unknown settings {sorted(unknown)}")
        locs = spec.get("locations", {})
        if not isinstance(locs, dict) or not all(isinstance(v, str) for v in locs.values()):
            err(f"{where}: locations must map names to path strings")
            locs = {}
        for lname, path in locs.items():
            if not path.startswith("/"):
                err(f"{where}.locations.{lname}: path must start with '/'")
            if "%20" in path:
                err(f"{where}.locations.{lname}: path is URL-encoded; write spaces, not %20")
            for ph in _PLACEHOLDER.findall(path):
                if ph not in fields:
                    err(f"{where}.locations.{lname}: placeholder {{{ph}}} is not a field")
        site_id = spec.get("site_id")
        if site_id is not None and (not isinstance(site_id, str) or not site_id):
            err(f"{where}: site_id must be a non-empty string")
        if not site_id and not locs:
            err(f"{where}: needs a site_id or at least one location")
        sites[key] = Site(key, str(spec.get("name") or key), site_id or None, dict(locs))

    if not sites:
        err(f"{sname}: no sites defined")
    if errors:
        raise ECMConfigError("invalid ECM config:\n  - " + "\n  - ".join(errors))
    return Catalog(env, doc_type, type_property, fields, kinds, sites,
                   (str(kinds_path), str(sites_path)))
