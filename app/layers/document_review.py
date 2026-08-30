"""Machine Readable Zone parsing and check-digit validation.

This is the one genuinely deterministic signal in the whole service: the MRZ
check digits are defined by ICAO Doc 9303 and either verify or they do not.
No model, no threshold, no confidence score -- a failed check digit means the
transcribed MRZ is internally inconsistent, full stop.

What it proves: the MRZ's own fields agree with their check digits.

What it does NOT prove:

- **That the document is genuine.** Check digits are a transcription-integrity
  mechanism, not an anti-forgery one -- anyone fabricating a document can
  compute valid check digits. Detecting a well-made forgery is what the human
  review step is for.
- **That an altered field will be caught.** Modulo-10 check digits are blind to
  any change whose weighted contribution shifts by a multiple of 10, so roughly
  one alteration in ten passes silently. See the test named
  `test_check_digits_miss_alterations_whose_weighted_delta_is_a_multiple_of_ten`
  for a worked example on the ICAO specimen.

So a passing MRZ moves a document to human review; it never approves one.
"""

from dataclasses import dataclass, field

# ICAO Doc 9303 check-digit weights, applied cyclically across a field.
_WEIGHTS = (7, 3, 1)

TD1_LINE_LENGTH = 30
TD2_LINE_LENGTH = 36
TD3_LINE_LENGTH = 44


class MrzFormatError(ValueError):
    """The supplied text is not a shape this parser recognises as an MRZ."""


def character_value(character: str) -> int:
    """Digits are themselves, A-Z are 10-35, and '<' (filler) is 0."""
    if character.isdigit():
        return int(character)
    if "A" <= character <= "Z":
        return ord(character) - ord("A") + 10
    if character == "<":
        return 0
    raise MrzFormatError(f"character {character!r} is not valid in an MRZ")


def compute_check_digit(field_text: str) -> int:
    """Weighted modulo-10 check digit over a field, per ICAO Doc 9303."""
    return sum(character_value(c) * _WEIGHTS[i % 3] for i, c in enumerate(field_text)) % 10


@dataclass
class CheckResult:
    name: str
    field_value: str
    expected: str
    actual: str

    @property
    def valid(self) -> bool:
        # A filler check digit means "not provided"; only a present digit is a
        # claim that can be contradicted.
        if self.actual == "<":
            return True
        return self.expected == self.actual


@dataclass
class MrzResult:
    document_format: str
    fields: dict[str, str] = field(default_factory=dict)
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def all_checks_valid(self) -> bool:
        return all(check.valid for check in self.checks)

    @property
    def failed_checks(self) -> list[str]:
        return [check.name for check in self.checks if not check.valid]


def _check(name: str, field_text: str, actual: str) -> CheckResult:
    return CheckResult(name=name, field_value=field_text, expected=str(compute_check_digit(field_text)), actual=actual)


def _normalise(text: str) -> list[str]:
    lines = [line.strip().upper().replace(" ", "<") for line in text.strip().splitlines()]
    return [line for line in lines if line]


def _parse_td3(lines: list[str]) -> MrzResult:
    """Passport: two lines of 44."""
    second = lines[1]
    fields = {
        "document_number": second[0:9],
        "nationality": second[10:13],
        "birth_date": second[13:19],
        "sex": second[20],
        "expiry_date": second[21:27],
        "personal_number": second[28:42],
        "surname_and_given_names": lines[0][5:],
        "issuing_state": lines[0][2:5],
    }
    composite = second[0:10] + second[13:20] + second[21:43]
    checks = [
        _check("document_number", fields["document_number"], second[9]),
        _check("birth_date", fields["birth_date"], second[19]),
        _check("expiry_date", fields["expiry_date"], second[27]),
        _check("personal_number", fields["personal_number"], second[42]),
        _check("composite", composite, second[43]),
    ]
    return MrzResult(document_format="TD3", fields=fields, checks=checks)


def _parse_td2(lines: list[str]) -> MrzResult:
    """Travel document / ID card: two lines of 36."""
    second = lines[1]
    fields = {
        "document_number": second[0:9],
        "nationality": second[10:13],
        "birth_date": second[13:19],
        "sex": second[20],
        "expiry_date": second[21:27],
        "optional_data": second[28:35],
        "issuing_state": lines[0][2:5],
    }
    composite = second[0:10] + second[13:20] + second[21:35]
    checks = [
        _check("document_number", fields["document_number"], second[9]),
        _check("birth_date", fields["birth_date"], second[19]),
        _check("expiry_date", fields["expiry_date"], second[27]),
        _check("composite", composite, second[35]),
    ]
    return MrzResult(document_format="TD2", fields=fields, checks=checks)


def _parse_td1(lines: list[str]) -> MrzResult:
    """ID card: three lines of 30."""
    first, second, third = lines[0], lines[1], lines[2]
    fields = {
        "document_number": first[5:14],
        "issuing_state": first[2:5],
        "birth_date": second[0:6],
        "sex": second[7],
        "expiry_date": second[8:14],
        "nationality": second[15:18],
        "surname_and_given_names": third,
    }
    composite = first[5:30] + second[0:7] + second[8:15] + second[18:29]
    checks = [
        _check("document_number", fields["document_number"], first[14]),
        _check("birth_date", fields["birth_date"], second[6]),
        _check("expiry_date", fields["expiry_date"], second[14]),
        _check("composite", composite, second[29]),
    ]
    return MrzResult(document_format="TD1", fields=fields, checks=checks)


def parse_mrz(text: str) -> MrzResult:
    """Parse an MRZ, choosing the format from its line count and width.

    Raises MrzFormatError if the text is not a recognised MRZ shape -- which is
    itself meaningful: unparseable text is not a passing document.
    """
    lines = _normalise(text)

    if len(lines) == 2 and all(len(line) == TD3_LINE_LENGTH for line in lines):
        return _parse_td3(lines)
    if len(lines) == 2 and all(len(line) == TD2_LINE_LENGTH for line in lines):
        return _parse_td2(lines)
    if len(lines) == 3 and all(len(line) == TD1_LINE_LENGTH for line in lines):
        return _parse_td1(lines)

    raise MrzFormatError(
        f"unrecognised MRZ shape: {len(lines)} line(s) of length {[len(line) for line in lines]}; "
        f"expected 2x{TD3_LINE_LENGTH} (TD3), 2x{TD2_LINE_LENGTH} (TD2) or 3x{TD1_LINE_LENGTH} (TD1)"
    )
