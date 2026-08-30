import pytest

from app.layers.document_review import (
    MrzFormatError,
    character_value,
    compute_check_digit,
    parse_mrz,
)

# The specimen published in ICAO Doc 9303 Part 4. Its check digits are fixed by
# the standard, so validating against it proves the algorithm, not just that our
# generator and parser agree with each other.
ICAO_TD3_SPECIMEN = (
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
)


def test_character_values_follow_the_standard():
    assert character_value("0") == 0
    assert character_value("9") == 9
    assert character_value("A") == 10
    assert character_value("Z") == 35
    assert character_value("<") == 0


def test_unexpected_character_is_rejected():
    with pytest.raises(MrzFormatError):
        character_value("!")


@pytest.mark.parametrize(
    "field_text,expected",
    [
        ("L898902C3", 6),  # document number, from the ICAO specimen
        ("740812", 2),  # date of birth
        ("120415", 9),  # expiry date
        ("ZE184226B<<<<<", 1),  # personal number
    ],
)
def test_check_digits_match_the_icao_specimen(field_text, expected):
    assert compute_check_digit(field_text) == expected


def test_icao_specimen_parses_and_fully_validates():
    result = parse_mrz(ICAO_TD3_SPECIMEN)

    assert result.document_format == "TD3"
    assert result.all_checks_valid is True
    assert result.failed_checks == []
    assert result.fields["document_number"] == "L898902C3"
    assert result.fields["nationality"] == "UTO"
    assert result.fields["birth_date"] == "740812"
    assert result.fields["expiry_date"] == "120415"
    assert result.fields["sex"] == "F"


def test_altering_a_field_breaks_its_check_digit():
    """The point of the check digits: a tampered field no longer agrees."""
    lines = ICAO_TD3_SPECIMEN.splitlines()
    tampered = lines[1][:5] + "9" + lines[1][6:]  # change one document-number character
    result = parse_mrz(f"{lines[0]}\n{tampered}")

    assert result.all_checks_valid is False
    assert "document_number" in result.failed_checks


def test_altering_a_field_also_breaks_the_composite_check():
    lines = ICAO_TD3_SPECIMEN.splitlines()
    tampered = lines[1][:13] + "750812" + lines[1][19:]  # change the birth date
    result = parse_mrz(f"{lines[0]}\n{tampered}")

    assert result.all_checks_valid is False
    assert "composite" in result.failed_checks


def test_check_digits_miss_alterations_whose_weighted_delta_is_a_multiple_of_ten():
    """A real and load-bearing limitation of modulo-10 check digits.

    They catch most single-character errors but are structurally blind to any
    change whose weighted contribution shifts by a multiple of 10 -- roughly
    one in ten random alterations. Changing this specimen's birth date from
    740812 to 800101 shifts the weighted sum by exactly 30, so the composite
    check digit is unchanged and the tampering passes that check.

    This is why a valid MRZ is evidence of transcription consistency only, and
    never of authenticity. Documented as a test so the property is not
    mistaken for a bug later.
    """
    lines = ICAO_TD3_SPECIMEN.splitlines()
    tampered = lines[1][:13] + "800101" + lines[1][19:]
    result = parse_mrz(f"{lines[0]}\n{tampered}")

    assert result.fields["birth_date"] == "800101"
    assert "composite" not in result.failed_checks  # the blind spot
    assert "birth_date" in result.failed_checks  # caught only by the field's own digit


def test_filler_check_digit_is_treated_as_absent_not_failed():
    """'<' in a check-digit position means 'not provided', not 'wrong'."""
    lines = ICAO_TD3_SPECIMEN.splitlines()
    # Blank out the optional personal number and its check digit.
    blanked = lines[1][:28] + "<" * 14 + "<" + lines[1][43]
    result = parse_mrz(f"{lines[0]}\n{blanked}")

    personal = next(c for c in result.checks if c.name == "personal_number")
    assert personal.valid is True


def test_lowercase_and_spaced_input_is_normalised():
    result = parse_mrz(ICAO_TD3_SPECIMEN.lower())
    assert result.all_checks_valid is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not an mrz",
        "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",  # only one line
        "TOO<SHORT\nALSO<SHORT",
    ],
)
def test_unrecognised_shapes_raise(text):
    with pytest.raises(MrzFormatError):
        parse_mrz(text)


def build_td1(document_number="D23145890", birth="740812", expiry="120415") -> str:
    """Construct a self-consistent TD1 to exercise the three-line path."""
    doc_check = compute_check_digit(document_number)
    line1 = f"I<UTO{document_number}{doc_check}<<<<<<<<<<<<<<<"
    birth_check = compute_check_digit(birth)
    expiry_check = compute_check_digit(expiry)
    partial2 = f"{birth}{birth_check}M{expiry}{expiry_check}UTO"
    optional2 = "<" * 11
    composite = compute_check_digit(line1[5:30] + partial2[0:7] + partial2[8:15] + optional2)
    line2 = f"{partial2}{optional2}{composite}"
    line3 = "ERIKSSON<<ANNA<MARIA<<<<<<<<<<"
    assert len(line1) == len(line2) == len(line3) == 30, (len(line1), len(line2), len(line3))
    return f"{line1}\n{line2}\n{line3}"


def test_td1_three_line_format_parses_and_validates():
    result = parse_mrz(build_td1())
    assert result.document_format == "TD1"
    assert result.all_checks_valid is True
    assert result.fields["document_number"] == "D23145890"


def test_td1_tampered_document_number_fails():
    mrz = build_td1()
    lines = mrz.splitlines()
    # 'D' -> 'E'. Deliberately not 'D' -> 'X': that shifts the weighted sum by
    # 140, a multiple of 10, which the check digit cannot see. See
    # test_check_digits_miss_alterations_whose_weighted_delta_is_a_multiple_of_ten.
    tampered = lines[0][:5] + "E" + lines[0][6:]
    result = parse_mrz("\n".join([tampered, lines[1], lines[2]]))
    assert result.all_checks_valid is False
    assert "document_number" in result.failed_checks
