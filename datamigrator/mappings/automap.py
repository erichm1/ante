"""Auto-mapping: suggest which source field feeds which target field, from the fields' names (and types).

For every target field it picks the best-scoring source field of the entity pair:

  100  same name                                    full_name  →  full_name
   96  same name, ignoring case and separators      fullName   →  full_name
   88  same field name inside a different object    address.city  →  city   /  location.city
   80  known equivalents (also Portuguese ↔ English)   telefone  →  phone,   cep  →  zip
   70  one name contains the other's key word        customer_email  →  email
   ≤76 similar spelling                              adress  →  address

then lowers the score when the types don't fit (an object into a string, say). Anything under `min_score` is not
suggested. Suggestions are saved as *draft* field mappings (FieldMapping.status = "draft"): they show on the canvas
but are left out of runs until somebody reviews and accepts them.
"""
import re
import unicodedata
from difflib import SequenceMatcher

from schemas.models import Field
from schemas.paths import leaf_fields

DEFAULT_MIN_SCORE = 60

# Names that mean the same thing. Every word is compared after normalising (lower case, no accents or separators).
_SYNONYM_GROUPS = [
    {"email", "mail", "emailaddress", "emailnfe", "correio"},
    {"phone", "telephone", "tel", "telefone", "mobile", "cell", "celular", "fone"},
    {"name", "nome", "fullname", "nomecompleto", "title", "titulo"},
    {"firstname", "givenname", "primeironome", "forename"},
    {"lastname", "surname", "familyname", "sobrenome"},
    {"description", "descricao", "desc", "details", "detalhes"},
    {"price", "preco", "amount", "valor", "total", "cost", "custo"},
    {"quantity", "quantidade", "qty", "qtd", "count", "stock", "estoque"},
    {"sku", "code", "codigo", "reference", "referencia", "ref", "partnumber"},
    {"zip", "zipcode", "postalcode", "postcode", "cep"},
    {"city", "cidade", "town", "municipio"},
    {"state", "estado", "province", "region", "uf"},
    {"country", "pais", "countrycode"},
    {"street", "address", "endereco", "logradouro", "addressline", "addressline1"},
    {"createdat", "created", "datacriacao", "criadoem", "creationdate", "datecreated"},
    {"updatedat", "updated", "dataalteracao", "atualizadoem", "modifiedat", "datemodified", "dataatualizacao"},
    {"birthdate", "dob", "datanascimento", "birthday", "dateofbirth", "nascimento"},
    {"company", "empresa", "organization", "organisation", "razaosocial"},
    {"document", "documento", "taxid", "cpfcnpj", "cnpj", "cpf", "vat"},
    {"active", "ativo", "enabled", "situacao", "status"},
    {"notes", "note", "observacoes", "observacao", "obs", "comments", "comment", "remarks"},
    {"image", "imagem", "photo", "foto", "picture", "img", "url"},
    {"category", "categoria", "group", "grupo"},
    {"brand", "marca", "manufacturer", "fabricante"},
    {"weight", "peso"},
    {"latitude", "lat"},
    {"longitude", "lng", "lon", "long"},
    {"currency", "moeda"},
]
_CANON = {}
for _i, _group in enumerate(_SYNONYM_GROUPS):
    for _word in _group:
        _CANON.setdefault(_word, _i)

GENERIC_TOKENS = {"id", "key", "code", "type", "status", "date", "value", "data", "ref", "number", "num", "no"}

_SCALAR = {Field.TYPE_STRING, Field.TYPE_NUMBER, Field.TYPE_INTEGER, Field.TYPE_BOOLEAN, Field.TYPE_DATE}
_NUMERIC = {Field.TYPE_NUMBER, Field.TYPE_INTEGER}


def _ascii(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def tokens(name):
    """"customerEmail", "customer_email" and "Customer-Email" all give ['customer', 'email']."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", _ascii(name))
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def norm(name):
    return "".join(tokens(name))


def last_segment(path):
    """The final, non-numeric part of a dotted path ("orders.0.sku" → "sku")."""
    parts = [p for p in path.split(".") if not p.isdigit()]
    return parts[-1] if parts else path


def type_penalty(source_type, target_type):
    if source_type == target_type:
        return 0
    if source_type in _SCALAR and target_type in _SCALAR:
        if target_type == Field.TYPE_STRING:
            return 0                                     # anything can be written as text
        if source_type in _NUMERIC and target_type in _NUMERIC:
            return 0
        if Field.TYPE_BOOLEAN in (source_type, target_type):
            return 10
        return 6
    if {source_type, target_type} == {Field.TYPE_OBJECT, Field.TYPE_ARRAY}:
        return 12
    return 45                                            # an object or list into a scalar (or the reverse)


def score_pair(source, target):
    """(score 0-100, reason) for wiring `source` into `target` — by name only; the type penalty is applied by the caller."""
    if source.name == target.name:
        return 100, "Same name"
    if norm(source.name) and norm(source.name) == norm(target.name):
        return 96, "Same name, ignoring case and separators"
    ls, lt = norm(last_segment(source.name)), norm(last_segment(target.name))
    if not ls or not lt:
        return 0, ""
    if ls == lt:
        return 88, "Same field name inside a different object"
    if ls in _CANON and _CANON.get(ls) == _CANON.get(lt):
        return 80, f"Known equivalent names ({last_segment(source.name)} ≈ {last_segment(target.name)})"

    ts, tt = set(tokens(last_segment(source.name))), set(tokens(last_segment(target.name)))
    shorter, longer = (ts, tt) if len(ts) <= len(tt) else (tt, ts)
    if shorter and shorter < longer:                     # every word of one name appears in the other
        meaningful = shorter - GENERIC_TOKENS
        if meaningful and max(len(t) for t in meaningful) >= 3:
            return 70, f"Shares the key word “{sorted(meaningful, key=len)[-1]}”"
        return 55, "Shares only a generic word"
    canon_s = {_CANON[t] for t in ts if t in _CANON}
    if canon_s and canon_s & {_CANON[t] for t in tt if t in _CANON} and len(ts) > 1 and len(tt) > 1:
        return 62, "Related words"
    ratio = SequenceMatcher(None, ls, lt).ratio()
    if ratio >= 0.8:
        return round(ratio * 85), f"Similar names ({round(ratio * 100)}%)"
    return 0, ""


def suggest(source_fields, target_fields, min_score=DEFAULT_MIN_SCORE, skip_targets=(), skip_pairs=()):
    """Suggestions for one entity pair.

    source_fields / target_fields  Field instances (objects that have members are skipped — only the innermost fields
                                  are wired, so an object isn't mapped twice).
    skip_targets                  target field ids that already have a wire and should be left alone.
    skip_pairs                    (source id, target id) pairs that are already wired.

    Returns (suggestions, unmatched_targets, unmatched_sources); a suggestion is
    {"source": Field, "target": Field, "score": int, "reason": str}, best-scoring first.
    """
    sources = leaf_fields(list(source_fields))
    targets = leaf_fields(list(target_fields))
    skip_targets, skip_pairs = set(skip_targets), set(skip_pairs)
    suggestions = []
    for target in targets:
        if target.pk in skip_targets:
            continue
        best = None
        for source in sources:
            if (source.pk, target.pk) in skip_pairs:
                continue
            base, reason = score_pair(source, target)
            if not base:
                continue
            score = max(0, base - type_penalty(source.field_type, target.field_type))
            if score < min_score:
                continue
            key = (score, source.field_type == target.field_type, -abs(len(source.name) - len(target.name)))
            if best is None or key > best[0]:
                best = (key, source, reason, score)
        if best:
            _, source, reason, score = best
            if source.field_type != target.field_type and score < 100:
                reason += f" — types differ ({source.field_type} → {target.field_type})"
            suggestions.append({"source": source, "target": target, "score": score, "reason": reason})
    suggestions.sort(key=lambda s: (-s["score"], s["target"].name))
    used_targets = {s["target"].pk for s in suggestions} | skip_targets
    used_sources = {s["source"].pk for s in suggestions} | {src for src, _ in skip_pairs}
    return (suggestions,
            [t for t in targets if t.pk not in used_targets],
            [s for s in sources if s.pk not in used_sources])
