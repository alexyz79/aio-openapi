import decimal
from dataclasses import Field, dataclass, field
from datetime import date, datetime
from numbers import Number
from typing import Any, Callable, Dict, Iterator, Tuple
from uuid import UUID

from dateutil.parser import parse as parse_date
from email_validator import EmailNotValidError, validate_email

from .. import json
from ..utils import compact_dict

REQUIRED = "required"
VALIDATOR = "OPENAPI_VALIDATOR"
DESCRIPTION = "description"
POST_PROCESS = "post_process"
DUMP = "dump"
FORMAT = "format"
OPS = "ops"

DataClass = Any


class ValidationError(ValueError):
    def __init__(self, field, message):
        self.field = field
        self.message = message


def data_field(
    required: bool = False,
    validator: Callable[[Field, Any, Dict], Any] = None,
    dump: Callable[[Any], Any] = None,
    format: str = None,
    description: str = None,
    post_process: Callable[[Any], Any] = None,
    ops: Tuple = (),
    **kwargs,
) -> Field:
    """Extend a dataclass field with

    :param validator: optional callable which accept (field, value, data)
                      as inputs and return the validated value
    :param required: boolean specifying if field is required
    :param dump: optional callable which receive the field value and convert to
                 the desired value to serve in requests
    :param format: optional string which represents the JSON schema format
    :params description: optional field description
    :params prost_process: post processor function executed after validation
    :param ops: optional tuple of strings specifying available operations
    """
    if isinstance(validator, Validator) and not dump:
        dump = validator.dump
    if "default_factory" not in kwargs:
        kwargs.setdefault("default", None)

    f = field(
        metadata=compact_dict(
            {
                VALIDATOR: validator,
                REQUIRED: required,
                DUMP: dump,
                DESCRIPTION: description,
                POST_PROCESS: post_process,
                FORMAT: format,
                OPS: ops,
            }
        ),
        **kwargs,
    )
    return f


def str_field(max_length=None, min_length=None, **kw) -> Field:
    kw.setdefault(
        "validator", StrValidator(min_length=min_length, max_length=max_length)
    )
    return data_field(**kw)


def bool_field(**kw) -> Field:
    kw.setdefault("validator", BoolValidator())
    return data_field(**kw)


def uuid_field(format="uuid", **kw) -> Field:
    """A UUID field with validation
    """
    kw.setdefault("validator", UUIDValidator())
    return data_field(format=format, **kw)


def number_field(min_value=None, max_value=None, precision=None, **kw) -> Field:
    kw.setdefault("validator", NumberValidator(min_value, max_value, precision))
    return data_field(**kw)


def integer_field(min_value=None, max_value=None, **kw) -> Field:
    kw.setdefault("validator", IntegerValidator(min_value, max_value))
    return data_field(**kw)


def decimal_field(min_value=None, max_value=None, precision=None, **kw) -> Field:
    kw.setdefault("validator", DecimalValidator(min_value, max_value, precision))
    return data_field(**kw)


def email_field(max_length=None, min_length=None, **kw) -> Field:
    kw.setdefault(
        "validator", EmailValidator(min_length=min_length, max_length=max_length)
    )
    return data_field(**kw)


def enum_field(EnumClass, **kw) -> Field:
    kw.setdefault("validator", EnumValidator(EnumClass))
    return data_field(**kw)


def date_field(**kw) -> Field:
    kw.setdefault("validator", DateValidator())
    return data_field(**kw)


def date_time_field(timezone=False, **kw) -> Field:
    kw.setdefault("validator", DateTimeValidator(timezone=timezone))
    return data_field(**kw)


def as_field(item, **kw) -> Field:
    if isinstance(item, Field):
        return item
    field = data_field(**kw)
    field.type = item
    return field


def json_field(**kw) -> Field:
    kw.setdefault("validator", JSONValidator())
    return data_field(**kw)


# Utilities


def field_ops(field: Field) -> Iterator[str]:
    yield field.name
    for op in field.metadata.get(OPS, ()):
        yield f"{field.name}:{op}"


# VALIDATORS


class Validator:
    dump = None

    def __call__(self, field, value, data=None):
        raise ValidationError(field.name, "invalid")

    def openapi(self, prop):
        pass


@dataclass
class StrValidator(Validator):
    min_length: int = 0
    max_length: int = 0

    def __call__(self, field, value, data=None):
        if not isinstance(value, str):
            raise ValidationError(field.name, "Must be a string")
        if self.min_length and len(value) < self.min_length:
            raise ValidationError(field.name, "Too short")
        if self.max_length and len(value) > self.max_length:
            raise ValidationError(field.name, "Too long")
        return value

    def openapi(self, prop):
        if self.min_length:
            prop["minLength"] = self.min_length
        if self.max_length:
            prop["maxLength"] = self.max_length


@dataclass
class EmailValidator(StrValidator):
    def __call__(self, field, value, data=None):
        value = super().__call__(field, value, data=data)
        try:
            validate_email(value, check_deliverability=False)
        except EmailNotValidError:
            raise ValidationError(field.name, "%s not a valid email" % value) from None
        return value


class ListValidator(Validator):
    def __init__(self, validators):
        self.validators = validators

    def __call__(self, field, value, data=None):
        for validator in self.validators:
            value = validator(field, value, data)
        return value

    def dump(self, value):
        for validator in self.validators:
            dump = getattr(validator, "dump", None)
            if hasattr(dump, "__call__"):
                value = dump(value)
        return value

    def openapi(self, prop):
        for validator in self.validators:
            if isinstance(validator, Validator):
                validator.openapi(prop)


class UUIDValidator(Validator):
    def __call__(self, field, value, data=None):
        try:
            if not isinstance(value, UUID):
                value = UUID(str(value))
            return value.hex
        except ValueError:
            raise ValidationError(field.name, "%s not a valid uuid" % value)

    def dump(self, value):
        if isinstance(value, UUID):
            return value.hex
        return value


class EnumValidator(Validator):
    """Enum validator to and from name (str) and value (int)"""

    def __init__(self, EnumClass):
        self.EnumClass = EnumClass

    def __call__(self, field, value, data=None):
        try:
            e = value
            if isinstance(e, str):
                e = getattr(self.EnumClass, value)
            if isinstance(e, self.EnumClass):
                return e if field.type == self.EnumClass else e.name
            raise AttributeError
        except AttributeError:
            raise ValidationError(field.name, "%s not valid" % value)

    def dump(self, value):
        if isinstance(value, self.EnumClass):
            return value.name
        return value


class Choice(Validator):
    def __init__(self, choices):
        self.choices = choices

    def __call__(self, field, value, data=None):
        if value not in self.choices:
            raise ValidationError(field.name, "%s not valid" % value)
        return value


class DateValidator(Validator):
    def dump(self, value):
        if isinstance(value, datetime):
            return value.date().isoformat()
        elif isinstance(value, date):
            return value.isoformat()
        return value

    def __call__(self, field, value, data=None):
        if isinstance(value, str):
            try:
                value = parse_date(value).date()
            except ValueError:
                pass
        if not isinstance(value, date):
            raise ValidationError(field.name, "%s not valid format" % value)
        return value


class DateTimeValidator(Validator):
    def __init__(self, timezone=False):
        self.timezone = timezone

    def dump(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def __call__(self, field, value, data=None):
        if isinstance(value, str):
            try:
                value = parse_date(value)
            except ValueError:
                pass
        if not isinstance(value, datetime):
            raise ValidationError(field.name, "%s not valid format" % value)
        if self.timezone and not value.tzinfo:
            raise ValidationError(field.name, "Timezone information required")
        return value


class BoundedNumberValidator(Validator):
    def __init__(self, min_value=None, max_value=None):
        self.min_value = min_value
        self.max_value = max_value

    def __call__(self, field, value, data=None):
        if self.min_value is not None and value < self.min_value:
            raise ValidationError(
                field.name, "%s less than %s" % (value, self.min_value)
            )
        if self.max_value is not None and value > self.max_value:
            raise ValidationError(
                field.name, "%s greater than %s" % (value, self.max_value)
            )
        return value

    def dump(self, value):
        return value

    def openapi(self, prop):
        if self.min_value is not None:
            prop["minimum"] = self.min_value
        if self.max_value is not None:
            prop["maximum"] = self.max_value


class NumberValidator(BoundedNumberValidator):
    def __init__(self, min_value=None, max_value=None, precision=None):
        super().__init__(min_value=min_value, max_value=max_value)
        self.precision = precision

    def __call__(self, field, value, data=None):
        try:
            if not isinstance(value, Number):
                raise TypeError

            if self.precision is not None:
                value = round(value, self.precision)

        except (ValueError, TypeError):
            raise ValidationError(field.name, "%s not valid number" % value)
        return super().__call__(field, value, data=data)

    def dump(self, value):
        if self.precision is not None:
            return round(value, self.precision)
        return value


class IntegerValidator(BoundedNumberValidator):
    def __call__(self, field, value, data=None):
        try:
            if isinstance(value, float):
                raise ValueError
            value = int(value)
        except (ValueError, TypeError):
            raise ValidationError(field.name, "%s not valid integer" % value)
        return super().__call__(field, value, data=data)


class DecimalValidator(NumberValidator):
    def __call__(self, field, value, data=None):
        try:
            if isinstance(value, float):
                value = str(value)
            value = decimal.Decimal(value)
        except (TypeError, decimal.InvalidOperation):
            raise ValidationError(field.name, "%s not valid Decimal" % value)
        return super().__call__(field, value, data=None)


class BoolValidator(Validator):
    def __call__(self, field, value, data=None):
        value = str(value).lower()
        if value not in ("true", "false"):
            raise ValidationError(field.name, "%s not valid" % value)
        return value == "true"

    def dump(self, value):
        return str(value).lower() == "true"


class JSONValidator(Validator):
    def __call__(self, field, value, data=None):
        try:
            return self.dump(value)
        except json.JSONDecodeError:
            raise ValidationError(field.name, "%s not valid" % value)

    def dump(self, value):
        return json.loads(value if isinstance(value, str) else json.dumps(value))
