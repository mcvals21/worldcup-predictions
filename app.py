from flask import Flask, render_template, request, redirect, url_for, abort, flash, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import secrets
import os
from sqlalchemy import text, inspect
import re
import unicodedata

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-this-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(     'DATABASE_URL',     'sqlite:///predictions.db' )
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

@app.context_processor
def inject_current_participant_token():
    return {
        'current_participant_token': session.get('participant_token')
    }

db = SQLAlchemy(app)

KUWAIT_TZ = ZoneInfo("Asia/Kuwait")

PARTICIPANT_NAMES = [
    'بو براك','بو ضاري','بو صقر','حمني','الحميدي','الخالدي','شافعي','الرشود',
    'العربيد','العومي','عيسى','الفزيع','فواز','القعود','ناصر','الوهيب'
]

PARTICIPANT_TOKENS = {
    'بو براك': 'bo-brak',
    'بو ضاري': 'bo-dhari',
    'بو صقر': 'bo-saqer',
    'حمني': 'hamni',
    'الحميدي': 'humaidi',
    'الخالدي': 'khaldi',
    'شافعي': 'shafie',
    'الرشود': 'rashood',
    'العربيد': 'arbeed',
    'العومي': 'awmi',
    'عيسى': 'essa',
    'الفزيع': 'fazaie',
    'فواز': 'fawaz',
    'القعود': 'alqaoud',
    'ناصر': 'nasser',
    'الوهيب': 'alwhaib'
}

STARTING_BONUS = {
    'الرشود': 2,
    'فواز': 1,
    'بو براك': 1,
    'بو صقر': 1,
    'حمني': 1,
    'العومي': 1
}

ADMIN_CODE = 'wc-admin-9Kx72LmQp2026-private'


class Tournament(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    champion_pick_deadline = db.Column(db.DateTime, nullable=True)


class Participant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    token = db.Column(db.String(40), unique=True, nullable=False)


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'), nullable=False)


class Match(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'), nullable=False)
    home_team = db.Column(db.String(80), nullable=False)
    away_team = db.Column(db.String(80), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    stage = db.Column(db.String(50), nullable=False, default='group')
    home_score = db.Column(db.Integer, nullable=True)
    away_score = db.Column(db.Integer, nullable=True)


class Prediction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey('participant.id'), nullable=False)
    match_id = db.Column(db.Integer, db.ForeignKey('match.id'), nullable=False)
    home_score = db.Column(db.Integer, nullable=False)
    away_score = db.Column(db.Integer, nullable=False)
    is_double = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('participant_id', 'match_id', name='unique_prediction'),)


class ChampionPick(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey('participant.id'), nullable=False)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'), nullable=False)
    team_name = db.Column(db.String(80), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('participant_id', 'tournament_id', name='unique_champion_pick'),)


class ChampionTeam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('tournament_id', 'name', name='unique_champion_team'),)


KNOCKOUT_STAGES = ['round32', 'round16', 'quarter', 'semi', 'final']

STAGE_LABELS = {
    'group': 'المجموعات',
    'round32': 'دور الـ32',
    'round16': 'دور الـ16',
    'quarter': 'ربع النهائي',
    'semi': 'نصف النهائي',
    'final': 'النهائي'
}

CHAMPION_TEAM_EXCLUDED_WORDS = [
    'TBD', 'To Be Determined', 'تحدد', 'لم يتحدد', 'غير محدد', 'غير معروف'
]


def is_real_champion_team(team_name):
    team = (team_name or '').strip()

    if not team:
        return False

    return not any(
        word.lower() in team.lower()
        for word in CHAMPION_TEAM_EXCLUDED_WORDS
    )


def _row_team_identity(row):
    return champion_team_identity(row.name)


def _is_arabic_text(text):
    return re.search(r'[\u0600-\u06FF]', text or '') is not None


def _is_short_code(text):
    clean = (text or '').strip()
    return bool(re.fullmatch(r'[A-Za-z]{2,4}', clean))


def preferred_team_display_name(current, candidate):
    """Choose one safe display name when the same team appears in variants.

    Examples: أمريكا/امريكا, أستراليا/استراليا, USA/United States.
    This only affects display; it does not delete any stored value.
    """
    current = (current or '').strip()
    candidate = (candidate or '').strip()

    if not current:
        return candidate
    if not candidate:
        return current

    current_ar = _is_arabic_text(current)
    candidate_ar = _is_arabic_text(candidate)

    # Prefer Arabic labels in this Arabic site.
    if candidate_ar and not current_ar:
        return candidate
    if current_ar and not candidate_ar:
        return current

    # Avoid showing short codes like USA/MEX when a full name exists.
    if _is_short_code(current) and not _is_short_code(candidate):
        return candidate
    if _is_short_code(candidate) and not _is_short_code(current):
        return current

    # Otherwise keep the existing name to avoid surprising changes.
    return current


def dedupe_team_names(team_names):
    """Return unique team names using team identity, not exact spelling.

    This prevents duplicates caused by:
    - أ / ا / إ / آ
    - Arabic/English variants when they map to the same flag/country
    - short codes such as USA vs United States
    """
    unique = {}

    for raw_name in team_names:
        name = (raw_name or '').strip()

        if not is_real_champion_team(name):
            continue

        identity = champion_team_identity(name)

        if not identity:
            continue

        unique[identity] = preferred_team_display_name(
            unique.get(identity),
            name
        )

    return sorted(unique.values(), key=normalize_team_key)


def scheduled_real_teams(tournament_id=None):
    """Return unique real team names currently present in the match schedule.

    This is only a source for building/updating the champion-pick list.
    It never deletes or hides anything.
    """
    query = Match.query

    if tournament_id is not None:
        query = query.filter(Match.tournament_id == tournament_id)

    raw_teams = []

    for m in query.all():
        raw_teams.extend([m.home_team, m.away_team])

    return dedupe_team_names(raw_teams)


def champion_team_list_exists(tournament_id):
    return ChampionTeam.query.filter_by(tournament_id=tournament_id).count() > 0


def unique_champion_team_rows(rows):
    """Return one visible management row per real team identity.

    Duplicate rows remain in the database for safety, but the admin and participant
    lists do not show the same team twice.
    """
    unique = {}

    for row in rows:
        identity = _row_team_identity(row)

        if not identity:
            continue

        current = unique.get(identity)

        if current is None:
            unique[identity] = row
            continue

        # Prefer an active row over a hidden duplicate.
        if row.is_active and not current.is_active:
            unique[identity] = row
            continue

        # If both have the same state, prefer the nicer display name.
        if row.is_active == current.is_active:
            preferred = preferred_team_display_name(current.name, row.name)
            if preferred == row.name:
                unique[identity] = row

    return sorted(
        unique.values(),
        key=lambda r: (not r.is_active, normalize_team_key(r.name))
    )


def champion_team_records(tournament_id):
    rows = ChampionTeam.query.filter_by(tournament_id=tournament_id).all()
    return unique_champion_team_rows(rows)


def champion_team_counts(tournament_id):
    source_count = len(scheduled_real_teams(tournament_id))
    rows = ChampionTeam.query.filter_by(tournament_id=tournament_id).all()

    if not rows:
        return {
            'total': 0,
            'active': source_count,
            'hidden': 0,
            'source_teams': source_count,
            'duplicates': 0
        }

    all_identities = {champion_team_identity(row.name) for row in rows if champion_team_identity(row.name)}
    active_identities = {
        champion_team_identity(row.name)
        for row in rows
        if row.is_active and champion_team_identity(row.name)
    }

    total_unique = len(all_identities)
    active = len(active_identities)
    hidden = max(total_unique - active, 0)
    duplicates = max(len(rows) - total_unique, 0)

    return {
        'total': total_unique,
        'active': active,
        'hidden': hidden,
        'source_teams': source_count,
        'duplicates': duplicates
    }


def _better_champion_team_row(current, candidate):
    """Choose which duplicate row should stay visible/active."""
    if current is None:
        return candidate

    # Active rows are safer to keep than hidden rows.
    if candidate.is_active and not current.is_active:
        return candidate
    if current.is_active and not candidate.is_active:
        return current

    preferred_name = preferred_team_display_name(current.name, candidate.name)

    if preferred_name == candidate.name and preferred_name != current.name:
        return candidate

    return current


def hide_duplicate_champion_teams(tournament_id):
    """Safely hide duplicate champion-team rows.

    No rows are deleted. If two rows represent the same team identity, only one stays
    active. Existing ChampionPick records remain untouched and are compared by
    identity elsewhere.
    """
    rows = ChampionTeam.query.filter_by(tournament_id=tournament_id).order_by(
        ChampionTeam.id.asc()
    ).all()

    grouped = {}

    for row in rows:
        identity = champion_team_identity(row.name)

        if not identity:
            continue

        grouped.setdefault(identity, []).append(row)

    hidden_count = 0

    for duplicates in grouped.values():
        if len(duplicates) <= 1:
            continue

        keeper = None

        for row in duplicates:
            keeper = _better_champion_team_row(keeper, row)

        for row in duplicates:
            if row.id != keeper.id and row.is_active:
                row.is_active = False
                hidden_count += 1

    return hidden_count


def sync_champion_teams_from_matches(tournament_id):
    """Add missing teams from the schedule to the champion list.

    Safety rules:
    - No delete.
    - No overwrite of hidden/active status for existing teams.
    - Newly discovered teams are active by default.
    - Duplicate spellings are treated as the same team identity.
    """
    added = 0

    existing_identities = {
        champion_team_identity(row.name)
        for row in ChampionTeam.query.filter_by(tournament_id=tournament_id).all()
    }

    for team_name in scheduled_real_teams(tournament_id):
        identity = champion_team_identity(team_name)

        if not identity or identity in existing_identities:
            continue

        db.session.add(ChampionTeam(
            tournament_id=tournament_id,
            name=team_name,
            is_active=True
        ))
        existing_identities.add(identity)
        added += 1

    hidden_duplicates = hide_duplicate_champion_teams(tournament_id)

    return added, hidden_duplicates


def champion_eligible_teams(tournament_id=None):
    """Return teams eligible for champion picks.

    Main behavior:
    - If the admin has created a champion-team list, use only teams marked active.
    - If the list has not been created yet, fall back to real teams in the schedule
      so the current site keeps working safely until the admin opens the new section.

    Hidden/non-qualified teams stay in the database for safety and audit purposes,
    but they do not appear as selectable choices.
    """
    if tournament_id is None:
        t = tournament()
        tournament_id = t.id if t else None

    if tournament_id is None:
        return []

    if champion_team_list_exists(tournament_id):
        rows = ChampionTeam.query.filter_by(
            tournament_id=tournament_id,
            is_active=True
        ).all()

        return dedupe_team_names(row.name for row in unique_champion_team_rows(rows))

    return scheduled_real_teams(tournament_id)


def matching_eligible_champion_team(team_name, eligible_teams):
    """Return the canonical selectable team name matching a submitted variant."""
    submitted_identity = champion_team_identity(team_name)

    if not submitted_identity:
        return None

    for eligible_team in eligible_teams:
        if champion_team_identity(eligible_team) == submitted_identity:
            return eligible_team

    return None


def is_champion_pick_valid(champion_pick, eligible_teams):
    return bool(
        champion_pick
        and matching_eligible_champion_team(champion_pick.team_name, eligible_teams)
    )


def same_champion_team(team_a, team_b):
    identity_a = champion_team_identity(team_a)
    identity_b = champion_team_identity(team_b)
    return bool(identity_a and identity_a == identity_b)


def champion_preview_teams(tournament_id=None):
    """Teams used only in the admin visual tester.
    This follows the same list seen by participants, with a schedule fallback before setup.
    """
    return champion_eligible_teams(tournament_id)


RAW_TEAM_FLAG_CODES = {
    # Group A
    'المكسيك': 'mx',
    'مكسيك': 'mx',
    'Mexico': 'mx',
    'MEX': 'mx',

    'جنوب أفريقيا': 'za',
    'جنوب افريقيا': 'za',
    'South Africa': 'za',
    'RSA': 'za',

    'كوريا الجنوبية': 'kr',
    'كوريا الجنوبيه': 'kr',
    'كوريا': 'kr',
    'South Korea': 'kr',
    'Korea Republic': 'kr',
    'KOR': 'kr',

    'التشيك': 'cz',
    'تشيكيا': 'cz',
    'جمهورية التشيك': 'cz',
    'Czechia': 'cz',
    'Czech Republic': 'cz',
    'CZE': 'cz',

    # Group B
    'كندا': 'ca',
    'Canada': 'ca',
    'CAN': 'ca',

    'البوسنة والهرسك': 'ba',
    'البوسنه والهرسك': 'ba',
    'البوسنة': 'ba',
    'البوسنه': 'ba',
    'Bosnia and Herzegovina': 'ba',
    'Bosnia': 'ba',
    'BIH': 'ba',

    'قطر': 'qa',
    'Qatar': 'qa',
    'QAT': 'qa',

    'سويسرا': 'ch',
    'Switzerland': 'ch',
    'SUI': 'ch',

    # Group C
    'البرازيل': 'br',
    'برازيل': 'br',
    'Brazil': 'br',
    'BRA': 'br',

    'المغرب': 'ma',
    'مغرب': 'ma',
    'Morocco': 'ma',
    'MAR': 'ma',

    'هايتي': 'ht',
    'هاييتي': 'ht',
    'Haiti': 'ht',
    'HTI': 'ht',

    'اسكتلندا': 'gb-sct',
    'إسكتلندا': 'gb-sct',
    'سكوتلندا': 'gb-sct',
    'سكوتلاندا': 'gb-sct',
    'Scotland': 'gb-sct',
    'SCO': 'gb-sct',

    # Group D
    'أمريكا': 'us',
    'امريكا': 'us',
    'الولايات المتحدة': 'us',
    'الولايات المتحده': 'us',
    'الولايات المتحدة الأمريكية': 'us',
    'United States': 'us',
    'USA': 'us',

    'باراغواي': 'py',
    'الباراغواي': 'py',
    'Paraguay': 'py',
    'PAR': 'py',

    'أستراليا': 'au',
    'استراليا': 'au',
    'Australia': 'au',
    'AUS': 'au',

    'تركيا': 'tr',
    'تركيا': 'tr',
    'Turkey': 'tr',
    'TUR': 'tr',

    # Group E
    'ألمانيا': 'de',
    'المانيا': 'de',
    'Germany': 'de',
    'GER': 'de',

    'كوراساو': 'cw',
    'كوراكاو': 'cw',
    'كوارساو': 'cw',
    'Curaçao': 'cw',
    'Curacao': 'cw',
    'CUW': 'cw',

    'ساحل العاج': 'ci',
    'كوت ديفوار': 'ci',
    'كوت ديفوار': 'ci',
    'Ivory Coast': 'ci',
    "Cote d'Ivoire": 'ci',
    'Côte d’Ivoire': 'ci',
    'CIV': 'ci',

    'الإكوادور': 'ec',
    'الاكوادور': 'ec',
    'إكوادور': 'ec',
    'اكوادور': 'ec',
    'Ecuador': 'ec',
    'ECU': 'ec',

    # Group F
    'هولندا': 'nl',
    'Netherlands': 'nl',
    'NED': 'nl',

    'اليابان': 'jp',
    'يابان': 'jp',
    'Japan': 'jp',
    'JPN': 'jp',

    'السويد': 'se',
    'سويد': 'se',
    'Sweden': 'se',
    'SWE': 'se',

    'تونس': 'tn',
    'Tunisia': 'tn',
    'TUN': 'tn',

    # Group G
    'بلجيكا': 'be',
    'Belgium': 'be',
    'BEL': 'be',

    'مصر': 'eg',
    'Egypt': 'eg',
    'EGY': 'eg',

    'إيران': 'ir',
    'ايران': 'ir',
    'Iran': 'ir',
    'IRI': 'ir',

    'نيوزيلندا': 'nz',
    'نيوزلندا': 'nz',
    'نيو زيلندا': 'nz',
    'New Zealand': 'nz',
    'NZL': 'nz',

    # Group H
    'إسبانيا': 'es',
    'اسبانيا': 'es',
    'Spain': 'es',
    'ESP': 'es',

    'الرأس الأخضر': 'cv',
    'الرأس الاخضر': 'cv',
    'راس الأخضر': 'cv',
    'راس الاخضر': 'cv',
    'كاب فيردي': 'cv',
    'Cape Verde': 'cv',
    'CPV': 'cv',

    'السعودية': 'sa',
    'السعوديه': 'sa',
    'السعودية': 'sa',
    'Saudi Arabia': 'sa',
    'KSA': 'sa',

    'الأوروغواي': 'uy',
    'الاوروغواي': 'uy',
    'أوروغواي': 'uy',
    'اورغواي': 'uy',
    'Uruguay': 'uy',
    'URU': 'uy',

    # Group I
    'فرنسا': 'fr',
    'France': 'fr',
    'FRA': 'fr',

    'السنغال': 'sn',
    'سنغال': 'sn',
    'Senegal': 'sn',
    'SEN': 'sn',

    'العراق': 'iq',
    'Iraq': 'iq',
    'IRQ': 'iq',

    'النرويج': 'no',
    'Norway': 'no',
    'NOR': 'no',

    # Group J
    'الأرجنتين': 'ar',
    'الارجنتين': 'ar',
    'أرجنتين': 'ar',
    'ارجنتين': 'ar',
    'Argentina': 'ar',
    'ARG': 'ar',

    'الجزائر': 'dz',
    'جزائر': 'dz',
    'Algeria': 'dz',
    'DZA': 'dz',

    'النمسا': 'at',
    'Austria': 'at',
    'AUT': 'at',

    'الأردن': 'jo',
    'الاردن': 'jo',
    'Jordan': 'jo',
    'JOR': 'jo',

    # Group K
    'البرتغال': 'pt',
    'برتغال': 'pt',
    'Portugal': 'pt',
    'POR': 'pt',

    'الكونغو الديمقراطية': 'cd',
    'الكونغو الديمقراطيه': 'cd',
    'الكونغو': 'cd',
    'كونغو': 'cd',
    'DR Congo': 'cd',
    'Congo DR': 'cd',
    'Democratic Republic of Congo': 'cd',
    'COD': 'cd',

    'أوزبكستان': 'uz',
    'اوزبكستان': 'uz',
    'Uzbekistan': 'uz',
    'UZB': 'uz',

    'كولومبيا': 'co',
    'Colombia': 'co',
    'COL': 'co',

    # Group L
    'إنجلترا': 'gb-eng',
    'انجلترا': 'gb-eng',
    'إنكلترا': 'gb-eng',
    'انكلترا': 'gb-eng',
    'England': 'gb-eng',
    'ENG': 'gb-eng',

    'كرواتيا': 'hr',
    'Croatia': 'hr',
    'CRO': 'hr',

    'غانا': 'gh',
    'Ghana': 'gh',
    'GHA': 'gh',

    'بنما': 'pa',
    'Panama': 'pa',
    'PAN': 'pa',
}


def normalize_team_key(name):
    text = (name or '').strip().lower()

    # Normalize Arabic hamza/alef variants and Latin accents such as Curaçao/Curacao.
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))

    replacements = {
        'أ': 'ا',
        'إ': 'ا',
        'آ': 'ا',
        'ٱ': 'ا',
        'ى': 'ي',
        'ئ': 'ي',
        'ؤ': 'و',
        'ة': 'ه',
        'ـ': ''
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r'[\.،,;:؛\-_/\(\)\[\]{}]+', ' ', text)

    return ' '.join(text.split())


TEAM_FLAG_CODES = {
    normalize_team_key(name): code
    for name, code in RAW_TEAM_FLAG_CODES.items()
}


def champion_team_identity(team_name):
    """Stable identity for comparing team names safely.

    If the name is known in the flag/alias table, all aliases share one identity.
    Example: أمريكا / امريكا / United States / USA => flag:us.
    Unknown names fall back to the normalized text.
    """
    key = normalize_team_key(team_name)

    if not key:
        return ''

    flag_code = TEAM_FLAG_CODES.get(key)

    if flag_code:
        return f'flag:{flag_code}'

    return f'name:{key}'


def team_flag_code(team_name):
    return TEAM_FLAG_CODES.get(normalize_team_key(team_name))


@app.context_processor
def inject_team_helpers():
    return {
        'team_flag_code': team_flag_code
    }


MATCH_FILTER_LABELS = {
    'open': 'المباريات الحالية',
    'previous': 'المباريات السابقة'
}

ADMIN_MATCH_FILTER_LABELS = {
    'needs_result': 'تحتاج نتيجة',
    'today': 'اليوم',
    'upcoming': 'القادمة',
    'finished': 'المنتهية',
    'all': 'كل المباريات'
}


def filter_admin_matches_for_view(matches, selected_filter):
    now = now_kw()

    if selected_filter == 'needs_result':
        return [
            m for m in matches
            if now >= m.start_time
            and (m.home_score is None or m.away_score is None)
        ]

    return filter_matches_for_view(matches, selected_filter)


def filter_matches_for_view(matches, selected_filter):
    now = now_kw()
    today_start = datetime.combine(now.date(), datetime.min.time())
    tomorrow_start = today_start + timedelta(days=1)

    if selected_filter in ['open', 'current', 'upcoming']:
        # Current matches: matches still open for prediction, nearest first.
        return [
            m for m in matches
            if not match_locked(m)
        ]

    if selected_filter == 'previous':
        # Previous matches: any match already locked/started, newest first.
        return sorted(
            [
                m for m in matches
                if match_locked(m)
            ],
            key=lambda m: m.start_time,
            reverse=True
        )

    if selected_filter == 'today':
        return [
            m for m in matches
            if today_start <= m.start_time < tomorrow_start
        ]

    if selected_filter == 'finished':
        return sorted(
            [
                m for m in matches
                if m.home_score is not None and m.away_score is not None
            ],
            key=lambda m: m.start_time,
            reverse=True
        )

    return matches


def now_kw():
    return datetime.now(KUWAIT_TZ).replace(tzinfo=None)


def match_locked(match):
    return now_kw() >= match.start_time


def winner(score_home, score_away):
    if score_home > score_away:
        return 'home'
    if score_home < score_away:
        return 'away'
    return 'draw'


def points_for(pred, match):
    if match.home_score is None or match.away_score is None:
        return 0

    is_knockout = match.stage in KNOCKOUT_STAGES

    exact_points = 5 if is_knockout else 3
    close_points = 2 if is_knockout else 1

    base = 0

    if pred.home_score == match.home_score and pred.away_score == match.away_score:
        base = exact_points
    elif winner(pred.home_score, pred.away_score) == winner(match.home_score, match.away_score):
        base = close_points

    multiplier = 2 if pred.is_double and is_knockout else 1

    return base * multiplier


def tournament():
    return Tournament.query.first()


def participant_by_token(token):
    p = Participant.query.filter_by(token=token).first()
    if not p:
        abort(404)
    return p


def current_double_used(participant_id, tournament_id, stage, exclude_match_id=None):
    q = db.session.query(Prediction).join(Match).filter(
        Prediction.participant_id == participant_id,
        Prediction.is_double == True,
        Match.tournament_id == tournament_id,
        Match.stage == stage
    )

    if exclude_match_id:
        q = q.filter(Prediction.match_id != exclude_match_id)

    return q.first()

def delete_match_external_refs(match_id):
    table_names = inspect(db.engine).get_table_names()

    if 'api_match_map' in table_names:
        db.session.execute(
            text('DELETE FROM api_match_map WHERE match_id = :match_id'),
            {'match_id': match_id}
        )


_database_ready = False


def ensure_database_ready():
    """Create only missing tables/seed rows. Never drops or clears live data."""
    global _database_ready

    if _database_ready:
        return

    db.create_all()

    if Tournament.query.first() is None:
        db.session.add(Tournament(name="كأس العالم 2026"))
        db.session.commit()

    if Participant.query.count() == 0:
        for name in PARTICIPANT_NAMES:
            db.session.add(Participant(
                name=name,
                token=PARTICIPANT_TOKENS[name]
            ))
        db.session.commit()

    _database_ready = True


@app.before_request
def before_request_ensure_database_ready():
    ensure_database_ready()


@app.route('/')
def index():
    return redirect(url_for('leaderboard'))


@app.route('/p/<token>', methods=['GET', 'POST'])
def participant_page(token):
    p = participant_by_token(token)
    session['participant_token'] = p.token

    t = tournament()

    if not t:
        abort(404)

    selected_filter = request.args.get('filter', 'open')

    if selected_filter not in MATCH_FILTER_LABELS:
        selected_filter = 'open'

    all_matches = Match.query.filter_by(
        tournament_id=t.id
    ).order_by(Match.start_time).all()

    matches = filter_matches_for_view(all_matches, selected_filter)

    match_counts = {
        key: len(filter_matches_for_view(all_matches, key))
        for key in MATCH_FILTER_LABELS
    }

    predictions = {
        x.match_id: x
        for x in Prediction.query.filter_by(participant_id=p.id).all()
    }

    champion = ChampionPick.query.filter_by(
        participant_id=p.id,
        tournament_id=t.id
    ).first()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'bulk_predict':
            match_ids = request.form.getlist('match_ids')

            saved_count = 0
            skipped_locked = 0
            selected_doubles_by_stage = {}

            for raw_match_id in match_ids:
                try:
                    match_id = int(raw_match_id)
                except (TypeError, ValueError):
                    continue

                match = Match.query.filter_by(
                    id=match_id,
                    tournament_id=t.id
                ).first()

                if not match:
                    continue

                if match_locked(match):
                    skipped_locked += 1
                    continue

                raw_hs = (request.form.get(f'home_score_{match.id}') or '').strip()
                raw_aw = (request.form.get(f'away_score_{match.id}') or '').strip()

                if raw_hs == '' or raw_aw == '':
                    flash('تنبيه: يرجى إدخال نتيجة لكل المباريات الحالية قبل الحفظ.')
                    return redirect(url_for(
                        'participant_page',
                        token=token,
                        filter=selected_filter
                    ))

                try:
                    hs = int(raw_hs)
                    aw = int(raw_aw)
                except ValueError:
                    flash('تنبيه: يرجى إدخال أرقام صحيحة فقط في مربعات التوقع.')
                    return redirect(url_for(
                        'participant_page',
                        token=token,
                        filter=selected_filter
                    ))

                is_double = request.form.get(f'is_double_{match.id}') == 'on'

                if is_double and match.stage not in KNOCKOUT_STAGES:
                    is_double = False

                if is_double:
                    if match.stage in selected_doubles_by_stage:
                        flash('لا يمكن اختيار أكثر من مباراة مضاعفة واحدة في نفس الدور.')
                        return redirect(url_for(
                            'participant_page',
                            token=token,
                            filter=selected_filter
                        ))

                    if current_double_used(p.id, t.id, match.stage, match.id):
                        flash('لا يمكن اختيار أكثر من مباراة مضاعفة واحدة في نفس الدور.')
                        return redirect(url_for(
                            'participant_page',
                            token=token,
                            filter=selected_filter
                        ))

                    selected_doubles_by_stage[match.stage] = match.id

                pred = predictions.get(match.id) or Prediction(
                    participant_id=p.id,
                    match_id=match.id
                )

                pred.home_score = hs
                pred.away_score = aw
                pred.is_double = is_double

                db.session.add(pred)
                predictions[match.id] = pred
                saved_count += 1

            db.session.commit()

            if saved_count:
                flash('تم حفظ توقعاتك بنجاح')
            elif skipped_locked:
                flash('لم يتم حفظ أي توقع لأن المباريات أصبحت مغلقة.')
            else:
                flash('لا توجد توقعات مفتوحة للحفظ.')

        elif action == 'predict':
            match = Match.query.get_or_404(int(request.form['match_id']))

            if match_locked(match):
                flash('تم إغلاق التوقع لهذه المباراة.')
                return redirect(url_for(
                    'participant_page',
                    token=token,
                    filter=selected_filter
                ))

            hs = int(request.form.get('home_score', 0))
            aw = int(request.form.get('away_score', 0))
            is_double = request.form.get('is_double') == 'on'

            if is_double and match.stage not in KNOCKOUT_STAGES:
                is_double = False

            if is_double and current_double_used(p.id, t.id, match.stage, match.id):
                flash('لا يمكن اختيار أكثر من مباراة مضاعفة واحدة في نفس الدور.')
                return redirect(url_for(
                    'participant_page',
                    token=token,
                    filter=selected_filter
                ))

            pred = predictions.get(match.id) or Prediction(
                participant_id=p.id,
                match_id=match.id
            )

            pred.home_score = hs
            pred.away_score = aw
            pred.is_double = is_double

            db.session.add(pred)
            db.session.commit()

            flash('تم حفظ التوقع.')

        elif action == 'champion':
            if t.champion_pick_deadline and now_kw() >= t.champion_pick_deadline:
                flash('تم إغلاق توقع البطل.')
            else:
                eligible_teams = champion_eligible_teams(t.id)
                team = request.form.get('team_name', '').strip()

                if not eligible_teams:
                    flash('سيتم فتح توقع بطل البطولة بعد إضافة المنتخبات إلى قائمة البطل.')
                elif not team:
                    flash('اختر منتخبًا أولًا.')
                else:
                    canonical_team = matching_eligible_champion_team(team, eligible_teams)

                    if not canonical_team:
                        flash('هذا المنتخب غير متاح لتوقع البطل.')
                    else:
                        pick = champion or ChampionPick(
                            participant_id=p.id,
                            tournament_id=t.id
                        )

                        pick.team_name = canonical_team

                        db.session.add(pick)
                        db.session.commit()

                        flash('تم حفظ توقع البطل.')

        return redirect(url_for(
            'participant_page',
            token=token,
            filter=selected_filter
        ))

    teams = champion_eligible_teams(t.id)
    champion_is_valid = is_champion_pick_valid(champion, teams)

    return render_template(
        'participant.html',
        p=p,
        t=t,
        matches=matches,
        predictions=predictions,
        locked=match_locked,
        points_for=points_for,
        champion=champion,
        champion_is_valid=champion_is_valid,
        teams=teams,
        stage_labels=STAGE_LABELS,
        knockout=KNOCKOUT_STAGES,
        selected_filter=selected_filter,
        match_filter_labels=MATCH_FILTER_LABELS,
        match_counts=match_counts,
        champion_locked=(
            t.champion_pick_deadline is not None
            and now_kw() >= t.champion_pick_deadline
        ),
        has_open_matches=any(not match_locked(m) for m in matches)
    )


@app.route('/p/<token>/champion', methods=['GET', 'POST'])
def participant_champion_page(token):
    """Participant champion pick page.
    Safe implementation: read eligible tournament teams, validate server-side, and only update ChampionPick.
    """
    p = participant_by_token(token)
    session['participant_token'] = p.token

    t = tournament()

    if not t:
        abort(404)

    champion = ChampionPick.query.filter_by(
        participant_id=p.id,
        tournament_id=t.id
    ).first()

    teams = champion_eligible_teams(t.id)
    champion_locked = (
        t.champion_pick_deadline is not None
        and now_kw() >= t.champion_pick_deadline
    )
    champion_is_valid = is_champion_pick_valid(champion, teams)

    if request.method == 'POST':
        if champion_locked:
            flash('تم إغلاق توقع البطل.')
        else:
            team = request.form.get('team_name', '').strip()

            if not teams:
                flash('سيتم فتح توقع بطل البطولة بعد إضافة المنتخبات إلى قائمة البطل.')
            elif not team:
                flash('اختر منتخبًا أولًا.')
            else:
                canonical_team = matching_eligible_champion_team(team, teams)

                if not canonical_team:
                    flash('هذا المنتخب غير متاح لتوقع البطل.')
                else:
                    pick = champion or ChampionPick(
                        participant_id=p.id,
                        tournament_id=t.id
                    )

                    pick.team_name = canonical_team

                    db.session.add(pick)
                    db.session.commit()

                    flash('تم حفظ توقع البطل.')

                    return redirect(url_for(
                        'participant_champion_page',
                        token=p.token
                    ))

        return redirect(url_for(
            'participant_champion_page',
            token=p.token
        ))

    return render_template(
        'champion.html',
        p=p,
        t=t,
        champion=champion,
        champion_is_valid=champion_is_valid,
        teams=teams,
        champion_locked=champion_locked
    )

@app.route('/rules')
def rules():
    t = tournament()
    return render_template('rules.html', t=t)

@app.route('/matches')
def matches_page():
    selected_filter = request.args.get('filter', 'open')

    if selected_filter not in MATCH_FILTER_LABELS:
        selected_filter = 'open'

    participant_token = session.get('participant_token')

    if participant_token:
        return redirect(url_for(
            'participant_page',
            token=participant_token,
            filter=selected_filter
        ))

    return redirect(url_for('leaderboard'))


@app.route('/today')
def today_page():
    return redirect(url_for('matches_page', filter='open'))

@app.route('/leaderboard')
def leaderboard():
    t = tournament()
    participants = Participant.query.order_by(Participant.name).all()
    rows = []

    final_match = Match.query.filter_by(
        tournament_id=t.id,
        stage='final'
    ).filter(
        Match.home_score.isnot(None),
        Match.away_score.isnot(None)
    ).first()

    champion_team = None

    if final_match:
        if final_match.home_score > final_match.away_score:
            champion_team = final_match.home_team
        elif final_match.away_score > final_match.home_score:
            champion_team = final_match.away_team

    for p in participants:
        preds = Prediction.query.filter_by(participant_id=p.id).all()

        starting_bonus = STARTING_BONUS.get(p.name, 0)
        pts = starting_bonus
        exact = 0
        champion_bonus = 0

        for pred in preds:
            match = Match.query.get(pred.match_id)

            if not match:
                continue

            pts += points_for(pred, match)

            if (
                match.home_score is not None
                and pred.home_score == match.home_score
                and pred.away_score == match.away_score
            ):
                exact += 1

        champion_pick = ChampionPick.query.filter_by(
            participant_id=p.id,
            tournament_id=t.id
        ).first()

        if champion_team and champion_pick and same_champion_team(champion_pick.team_name, champion_team):
            champion_bonus = 10
            pts += champion_bonus

        rows.append({
            'name': p.name,
            'points': pts,
            'exact': exact,
            'champion_bonus': champion_bonus,
            'starting_bonus': starting_bonus,
            'champion_pick_team': champion_pick.team_name if champion_pick else None,
            'champion_pick_flag': team_flag_code(champion_pick.team_name) if champion_pick else None
        })

    rows.sort(
        key=lambda r: (r['points'], r['exact'], r['champion_bonus']),
        reverse=True
    )

    champion_picks_visible = (
        t.champion_pick_deadline is not None
        and now_kw() >= t.champion_pick_deadline
    )

    return render_template(
        'leaderboard.html',
        rows=rows,
        t=t,
        champion_picks_visible=champion_picks_visible
    )


@app.route('/stats')
def stats():
    t = tournament()

    if not t:
        abort(404)

    participants = Participant.query.order_by(Participant.name).all()

    all_matches = Match.query.filter_by(
        tournament_id=t.id
    ).order_by(Match.start_time).all()

    match_ids = [m.id for m in all_matches]

    match_map = {
        m.id: m
        for m in all_matches
    }

    completed_matches = [
        m for m in all_matches
        if m.home_score is not None and m.away_score is not None
    ]

    locked_matches = [
        m for m in all_matches
        if match_locked(m)
    ]

    if match_ids:
        total_predictions = Prediction.query.filter(
            Prediction.match_id.in_(match_ids)
        ).count()
    else:
        total_predictions = 0

    total_possible_predictions = len(participants) * len(locked_matches)

    if total_possible_predictions:
        participation_rate = round(
            (total_predictions / total_possible_predictions) * 100
        )
    else:
        participation_rate = 0

    player_rows = []

    for p in participants:
        preds = Prediction.query.filter_by(
            participant_id=p.id
        ).all()

        preds_by_match = {
            pred.match_id: pred
            for pred in preds
            if pred.match_id in match_ids
        }

        exact = 0
        match_points = 0
        x2_used = 0
        x2_extra_points = 0
        x2_total_points = 0

        for m in completed_matches:
            pred = preds_by_match.get(m.id)

            if not pred:
                continue

            pts = points_for(pred, m)
            match_points += pts

            if pred.home_score == m.home_score and pred.away_score == m.away_score:
                exact += 1

            if pred.is_double and m.stage in KNOCKOUT_STAGES:
                x2_used += 1
                base_points = pts // 2
                extra_points = pts - base_points
                x2_extra_points += extra_points
                x2_total_points += pts

        missed_locked = sum(
            1 for m in locked_matches
            if m.id not in preds_by_match
        )

        player_rows.append({
            'name': p.name,
            'exact': exact,
            'predictions_count': len(preds_by_match),
            'missed_locked': missed_locked,
            'match_points': match_points,
            'x2_used': x2_used,
            'x2_extra_points': x2_extra_points,
            'x2_total_points': x2_total_points
        })

    top_exact = sorted(
        player_rows,
        key=lambda r: (r['exact'], r['match_points']),
        reverse=True
    )[:5]

    top_participation = sorted(
        player_rows,
        key=lambda r: (r['predictions_count'], r['match_points']),
        reverse=True
    )[:5]

    most_missed = sorted(
        [
            r for r in player_rows
            if r['missed_locked'] > 0
        ],
        key=lambda r: r['missed_locked'],
        reverse=True
    )[:5]

    top_x2 = sorted(
        [
            r for r in player_rows
            if r['x2_extra_points'] > 0
        ],
        key=lambda r: (
            r['x2_extra_points'],
            r['x2_total_points'],
            r['x2_used']
        ),
        reverse=True
    )[:5]

    match_rows = []

    for m in completed_matches:
        preds = Prediction.query.filter_by(match_id=m.id).all()

        total_points = sum(points_for(pred, m) for pred in preds)

        exact_count = sum(
            1 for pred in preds
            if pred.home_score == m.home_score and pred.away_score == m.away_score
        )

        match_rows.append({
            'match': m,
            'total_predictions': len(preds),
            'total_points': total_points,
            'exact_count': exact_count
        })

    predicted_match_rows = [
        r for r in match_rows
        if r['total_predictions'] > 0
    ]

    top_point_matches = sorted(
        predicted_match_rows,
        key=lambda r: (
            r['total_points'],
            r['exact_count'],
            r['total_predictions']
        ),
        reverse=True
    )[:5]

    hardest_matches = sorted(
        predicted_match_rows,
        key=lambda r: (
            r['total_points'],
            r['exact_count'],
            r['total_predictions']
        )
    )[:5]

    return render_template(
        'stats.html',
        t=t,
        total_participants=len(participants),
        total_matches=len(all_matches),
        completed_matches_count=len(completed_matches),
        locked_matches_count=len(locked_matches),
        total_predictions=total_predictions,
        participation_rate=participation_rate,
        top_exact=top_exact,
        top_participation=top_participation,
        most_missed=most_missed,
        top_x2=top_x2,
        top_point_matches=top_point_matches,
        hardest_matches=hardest_matches,
        stage_labels=STAGE_LABELS
    )

@app.route('/match/<int:match_id>')
def match_view(match_id):
    match = Match.query.get_or_404(match_id)

    if not match_locked(match):
        return 'التوقعات سرية حتى بداية المباراة.'

    preds = db.session.query(Prediction, Participant).join(Participant).filter(
        Prediction.match_id == match_id
    ).all()

    return render_template(
        'match.html',
        match=match,
        preds=preds,
        points_for=points_for
    )


def parse_match_id_list(raw_ids):
    ids = []

    for part in (raw_ids or '').split(','):
        part = part.strip()

        if not part:
            continue

        try:
            match_id = int(part)
        except ValueError:
            continue

        if match_id not in ids:
            ids.append(match_id)

    # Safety limit: this dashboard is for one round, not the full tournament.
    return ids[:12]


ROUND_STATS_EXCLUDED_NAMES = {'الحميدي', 'العومي'}


def build_round_dashboard(matches, participants):
    participants = [
        p for p in participants
        if p.name not in ROUND_STATS_EXCLUDED_NAMES
    ]

    active_participant_ids = [p.id for p in participants]
    match_ids = [m.id for m in matches]

    if not match_ids or not active_participant_ids:
        return {
            'player_rows': [],
            'top_players': [],
            'top_x2': [],
            'match_rows': [],
            'easiest_matches': [],
            'hardest_matches': [],
            'total_points': 0,
            'total_exact': 0,
            'total_predictions': 0,
            'total_missed': 0
        }

    predictions = Prediction.query.filter(
        Prediction.match_id.in_(match_ids),
        Prediction.participant_id.in_(active_participant_ids)
    ).all()

    match_map = {
        m.id: m
        for m in matches
    }

    preds_by_participant = {}

    for pred in predictions:
        preds_by_participant.setdefault(
            pred.participant_id,
            {}
        )[pred.match_id] = pred

    player_rows = []

    for p in participants:
        participant_preds = preds_by_participant.get(p.id, {})

        points = 0
        exact = 0
        predictions_count = 0
        missed = 0
        x2_used = 0
        x2_extra_points = 0
        x2_total_points = 0

        for m in matches:
            pred = participant_preds.get(m.id)

            if not pred:
                missed += 1
                continue

            predictions_count += 1

            pts = points_for(pred, m)
            points += pts

            if pred.home_score == m.home_score and pred.away_score == m.away_score:
                exact += 1

            if pred.is_double and m.stage in KNOCKOUT_STAGES:
                x2_used += 1
                extra = pts // 2
                x2_extra_points += extra
                x2_total_points += pts

        player_rows.append({
            'name': p.name,
            'points': points,
            'exact': exact,
            'predictions_count': predictions_count,
            'missed': missed,
            'x2_used': x2_used,
            'x2_extra_points': x2_extra_points,
            'x2_total_points': x2_total_points
        })

    player_rows.sort(
        key=lambda r: (
            r['points'],
            r['exact'],
            r['predictions_count']
        ),
        reverse=True
    )

    top_players = player_rows[:5]

    top_x2 = sorted(
        [
            r for r in player_rows
            if r['x2_extra_points'] > 0
        ],
        key=lambda r: (
            r['x2_extra_points'],
            r['x2_total_points'],
            r['x2_used']
        ),
        reverse=True
    )[:5]

    match_rows = []

    for m in matches:
        match_predictions = [
            pred
            for pred in predictions
            if pred.match_id == m.id
        ]

        total_points = sum(
            points_for(pred, m)
            for pred in match_predictions
        )

        exact_count = sum(
            1
            for pred in match_predictions
            if pred.home_score == m.home_score and pred.away_score == m.away_score
        )

        match_rows.append({
            'match': m,
            'total_predictions': len(match_predictions),
            'total_points': total_points,
            'exact_count': exact_count
        })

    predicted_match_rows = [
        r for r in match_rows
        if r['total_predictions'] > 0
    ]

    easiest_matches = sorted(
        predicted_match_rows,
        key=lambda r: (
            r['total_points'],
            r['exact_count'],
            r['total_predictions']
        ),
        reverse=True
    )[:3]

    hardest_matches = sorted(
        predicted_match_rows,
        key=lambda r: (
            r['total_points'],
            r['exact_count'],
            r['total_predictions']
        )
    )[:3]

    return {
        'player_rows': player_rows,
        'top_players': top_players,
        'top_x2': top_x2,
        'match_rows': match_rows,
        'easiest_matches': easiest_matches,
        'hardest_matches': hardest_matches,
        'total_points': sum(r['points'] for r in player_rows),
        'total_exact': sum(r['exact'] for r in player_rows),
        'total_predictions': sum(r['predictions_count'] for r in player_rows),
        'total_missed': sum(r['missed'] for r in player_rows)
    }


@app.route('/admin/<code>/round-stats')
def round_stats_builder(code):
    if code != ADMIN_CODE:
        abort(404)

    t = tournament()

    if not t:
        abort(404)

    completed_matches = Match.query.filter_by(
        tournament_id=t.id
    ).filter(
        Match.home_score.isnot(None),
        Match.away_score.isnot(None)
    ).order_by(
        Match.start_time.desc()
    ).all()

    return render_template(
        'round_stats_builder.html',
        t=t,
        code=code,
        matches=completed_matches,
        round_stats_url=url_for('round_stats', _external=True),
        stage_labels=STAGE_LABELS
    )


@app.route('/stats/round')
def round_stats():
    t = tournament()

    if not t:
        abort(404)

    raw_ids = request.args.get('matches', '')
    match_ids = parse_match_id_list(raw_ids)

    matches = []

    if match_ids:
        found_matches = Match.query.filter(
            Match.tournament_id == t.id,
            Match.id.in_(match_ids)
        ).all()

        match_map = {
            m.id: m
            for m in found_matches
            if m.home_score is not None and m.away_score is not None
        }

        matches = [
            match_map[match_id]
            for match_id in match_ids
            if match_id in match_map
        ]

    participants = Participant.query.order_by(Participant.name).all()

    dashboard = build_round_dashboard(matches, participants)

    share_url = url_for(
        'round_stats',
        matches=','.join(str(m.id) for m in matches),
        _external=True
    )

    return render_template(
        'round_stats.html',
        t=t,
        matches=matches,
        dashboard=dashboard,
        share_url=share_url,
        stage_labels=STAGE_LABELS
    )



def _new_team_stat(name):
    return {
        'name': name,
        'played': 0,
        'wins': 0,
        'draws': 0,
        'losses': 0,
        'gf': 0,
        'ga': 0,
        'gd': 0,
        'points': 0,
        'clean_sheets': 0,
        'form': []
    }


def _champion_stats_result_letter(goals_for, goals_against):
    if goals_for > goals_against:
        return 'W'
    if goals_for == goals_against:
        return 'D'
    return 'L'


def _champion_form_score(form):
    score_map = {
        'W': 3,
        'D': 1,
        'L': 0
    }

    return sum(score_map.get(item, 0) for item in form[-3:])


def build_champion_stats_dashboard(tournament_id):
    """Build an admin-only, share-by-screenshot champion selection dashboard.

    Safety notes:
    - Reads existing completed matches only.
    - Does not write to the database.
    - Uses the active ChampionTeam list when available, so after you hide
      eliminated teams the dashboard focuses on the remaining choices.
    """
    champion_rows = ChampionTeam.query.filter_by(
        tournament_id=tournament_id
    ).order_by(
        ChampionTeam.name.asc()
    ).all()

    active_champion_rows = [
        row for row in champion_rows
        if row.is_active and is_real_champion_team(row.name)
    ]

    list_exists = bool(champion_rows)
    active_identities = {
        champion_team_identity(row.name)
        for row in active_champion_rows
        if champion_team_identity(row.name)
    }

    display_names = {}

    for row in active_champion_rows:
        identity = champion_team_identity(row.name)

        if not identity:
            continue

        display_names[identity] = preferred_team_display_name(
            display_names.get(identity),
            row.name
        )

    completed_matches = Match.query.filter_by(
        tournament_id=tournament_id
    ).filter(
        Match.home_score.isnot(None),
        Match.away_score.isnot(None)
    ).order_by(
        Match.start_time.asc()
    ).all()

    stats_by_identity = {}

    def should_include(team_name):
        if not is_real_champion_team(team_name):
            return False

        identity = champion_team_identity(team_name)

        if not identity:
            return False

        # If the admin has built the champion list, focus only on active teams.
        # If the list is not ready yet, fall back to all real teams with results.
        if active_identities and identity not in active_identities:
            return False

        return True

    def ensure_team(team_name):
        identity = champion_team_identity(team_name)

        if identity not in stats_by_identity:
            display_name = preferred_team_display_name(
                display_names.get(identity),
                team_name
            )
            stats_by_identity[identity] = _new_team_stat(display_name)

        return stats_by_identity[identity]

    for match in completed_matches:
        home_name = match.home_team
        away_name = match.away_team
        home_goals = int(match.home_score)
        away_goals = int(match.away_score)

        if should_include(home_name):
            home = ensure_team(home_name)
            home['played'] += 1
            home['gf'] += home_goals
            home['ga'] += away_goals
            home['gd'] = home['gf'] - home['ga']
            home['points'] += 3 if home_goals > away_goals else 1 if home_goals == away_goals else 0
            home['wins'] += 1 if home_goals > away_goals else 0
            home['draws'] += 1 if home_goals == away_goals else 0
            home['losses'] += 1 if home_goals < away_goals else 0
            home['clean_sheets'] += 1 if away_goals == 0 else 0
            home['form'].append(_champion_stats_result_letter(home_goals, away_goals))

        if should_include(away_name):
            away = ensure_team(away_name)
            away['played'] += 1
            away['gf'] += away_goals
            away['ga'] += home_goals
            away['gd'] = away['gf'] - away['ga']
            away['points'] += 3 if away_goals > home_goals else 1 if away_goals == home_goals else 0
            away['wins'] += 1 if away_goals > home_goals else 0
            away['draws'] += 1 if away_goals == home_goals else 0
            away['losses'] += 1 if away_goals < home_goals else 0
            away['clean_sheets'] += 1 if home_goals == 0 else 0
            away['form'].append(_champion_stats_result_letter(away_goals, home_goals))

    rows = list(stats_by_identity.values())

    for row in rows:
        played = row['played'] or 1
        row['ga_per_match'] = row['ga'] / played
        row['gf_per_match'] = row['gf'] / played
        row['form_last'] = row['form'][-3:]
        row['form_score'] = _champion_form_score(row['form'])
        row['power_score'] = round(
            (row['points'] * 10)
            + (row['wins'] * 4)
            + (row['gd'] * 3)
            + (row['gf'] * 1.5)
            + (row['clean_sheets'] * 3)
            + (row['form_score'] * 2)
            - (row['ga'] * 1.5),
            1
        )

    def top(limit, key):
        return sorted(rows, key=key, reverse=True)[:limit]

    attack = top(5, lambda r: (r['gf'], r['gf_per_match'], r['gd'], r['points']))
    defense = sorted(
        rows,
        key=lambda r: (r['ga_per_match'], r['ga'], -r['clean_sheets'], -r['points'], -r['gd'])
    )[:5]
    goal_difference = top(5, lambda r: (r['gd'], r['points'], r['gf']))
    wins = top(5, lambda r: (r['wins'], r['points'], r['gd']))
    clean_sheets = top(5, lambda r: (r['clean_sheets'], -r['ga'], r['points'], r['gd']))
    contenders = top(8, lambda r: (r['power_score'], r['points'], r['gd'], r['gf']))

    return {
        'rows': sorted(rows, key=lambda r: (r['points'], r['gd'], r['gf']), reverse=True),
        'attack': attack,
        'defense': defense,
        'goal_difference': goal_difference,
        'wins': wins,
        'clean_sheets': clean_sheets,
        'contenders': contenders,
        'completed_matches_count': len(completed_matches),
        'active_teams_count': len(active_identities) if active_identities else len(rows),
        'list_exists': list_exists,
        'using_active_list': bool(active_identities)
    }


@app.route('/admin/<code>/champion-stats')
def admin_champion_stats(code):
    if code != ADMIN_CODE:
        abort(404)

    t = tournament()

    if not t:
        abort(404)

    dashboard = build_champion_stats_dashboard(t.id)

    return render_template(
        'champion_stats_admin.html',
        t=t,
        code=code,
        dashboard=dashboard,
        generated_at=now_kw()
    )


@app.route('/admin/<code>', methods=['GET', 'POST'])
def admin(code):
    if code != ADMIN_CODE:
        abort(404)

    t = tournament()

    if not t:
        abort(404)

    selected_filter = request.args.get('filter', 'needs_result')

    if selected_filter not in ADMIN_MATCH_FILTER_LABELS:
        selected_filter = 'needs_result'

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_match':
            # New compact form uses separate date/time fields.
            # Keep backward compatibility with the old datetime-local field.
            if request.form.get('match_date') and request.form.get('match_time'):
                dt = datetime.strptime(
                    f"{request.form['match_date']} {request.form['match_time']}",
                    '%Y-%m-%d %H:%M'
                )
            else:
                dt = datetime.strptime(
                    request.form['start_time'],
                    '%Y-%m-%dT%H:%M'
                )

            m = Match(
                tournament_id=t.id,
                home_team=request.form['home_team'].strip(),
                away_team=request.form['away_team'].strip(),
                start_time=dt,
                stage=request.form['stage']
            )

            db.session.add(m)
            db.session.commit()

            flash('تمت إضافة المباراة بنجاح.')

        elif action == 'result':
            m = Match.query.get_or_404(int(request.form['match_id']))

            m.home_score = int(request.form['home_score'])
            m.away_score = int(request.form['away_score'])

            db.session.commit()

            flash('تم حفظ النتيجة وتحديث النقاط.')

        elif action == 'clear_result':
            m = Match.query.get_or_404(int(request.form['match_id']))

            m.home_score = None
            m.away_score = None

            db.session.commit()

            flash('تم مسح النتيجة. التوقعات بقيت محفوظة.')

        elif action == 'edit_match':
            m = Match.query.get_or_404(int(request.form['match_id']))

            m.home_team = request.form['home_team'].strip()
            m.away_team = request.form['away_team'].strip()
            m.stage = request.form['stage']

            m.start_time = datetime.strptime(
                request.form['start_time'],
                '%Y-%m-%dT%H:%M'
            )

            db.session.commit()

            flash('تم تحديث المباراة.')

        elif action == 'delete_match':
            m = Match.query.get_or_404(int(request.form['match_id']))

            Prediction.query.filter_by(match_id=m.id).delete()
            delete_match_external_refs(m.id)

            db.session.delete(m)
            db.session.commit()

            flash('تم حذف المباراة.')
            
        elif action == 'champion_deadline':
            raw_deadline = request.form.get('deadline', '').strip()

            if raw_deadline:
                try:
                    t.champion_pick_deadline = datetime.strptime(
                        raw_deadline,
                        '%Y-%m-%dT%H:%M'
                    )
                    db.session.commit()
                    flash('تم حفظ موعد إغلاق توقع البطل.')
                except ValueError:
                    flash('صيغة موعد إغلاق توقع البطل غير صحيحة.')
            else:
                t.champion_pick_deadline = None
                db.session.commit()
                flash('تم مسح موعد إغلاق توقع البطل.')

        elif action == 'clear_champion_deadline':
            t.champion_pick_deadline = None
            db.session.commit()
            flash('تم مسح موعد إغلاق توقع البطل.')

        elif action == 'sync_champion_teams':
            added_count, hidden_duplicates = sync_champion_teams_from_matches(t.id)
            db.session.commit()

            if added_count or hidden_duplicates:
                message_parts = []

                if added_count:
                    message_parts.append(f'تمت إضافة {added_count} منتخب إلى قائمة اختيار البطل')

                if hidden_duplicates:
                    message_parts.append(f'تم إخفاء {hidden_duplicates} اسم مكرر بأمان')

                flash('، و'.join(message_parts) + '. لم يتم حذف أي بيانات.')
            else:
                flash('قائمة اختيار البطل محدثة بالفعل. لم يتم حذف أو تغيير أي بيانات.')

        elif action == 'add_champion_team':
            team_name = request.form.get('team_name', '').strip()

            if not is_real_champion_team(team_name):
                flash('اسم المنتخب غير صالح أو يبدو كاسم مؤقت مثل TBD.')
            else:
                existing = None
                requested_identity = champion_team_identity(team_name)

                for row in ChampionTeam.query.filter_by(tournament_id=t.id).all():
                    if champion_team_identity(row.name) == requested_identity:
                        existing = row
                        break

                if existing:
                    existing.is_active = True
                    db.session.commit()
                    flash('المنتخب موجود مسبقًا وتم جعله متاحًا للاختيار.')
                else:
                    db.session.add(ChampionTeam(
                        tournament_id=t.id,
                        name=team_name,
                        is_active=True
                    ))
                    db.session.commit()
                    flash('تمت إضافة المنتخب إلى قائمة اختيار البطل.')

        elif action == 'update_champion_teams':
            active_ids = set()

            for raw_team_id in request.form.getlist('active_team_ids'):
                try:
                    active_ids.add(int(raw_team_id))
                except (TypeError, ValueError):
                    continue

            teams_to_update = ChampionTeam.query.filter_by(tournament_id=t.id).all()

            for team_row in teams_to_update:
                team_row.is_active = team_row.id in active_ids

            db.session.commit()
            flash('تم حفظ قائمة منتخبات البطل. المنتخبات غير المحددة أصبحت مخفية من الاختيار وليست محذوفة.')

        elif action == 'delete_empty_english_matches':
            all_matches = Match.query.filter_by(tournament_id=t.id).all()

            matches_to_delete = []

            for m in all_matches:
                teams_text = f'{m.home_team} {m.away_team}'
                has_english = re.search(r'[A-Za-z]', teams_text) is not None

                if not has_english:
                    continue

                pred_count = Prediction.query.filter_by(match_id=m.id).count()

                if pred_count == 0:
                    matches_to_delete.append(m)

            deleted_count = 0

            for m in matches_to_delete:
                delete_match_external_refs(m.id)
                db.session.delete(m)
                deleted_count += 1

            db.session.commit()

            flash(f'تم حذف {deleted_count} مباراة إنجليزية قديمة بلا توقعات.')

        return redirect(url_for(
            'admin',
            code=code,
            filter=selected_filter
        ))

    all_matches = Match.query.filter_by(
        tournament_id=t.id
    ).order_by(Match.start_time).all()

    matches = filter_admin_matches_for_view(all_matches, selected_filter)

    match_counts = {
        key: len(filter_admin_matches_for_view(all_matches, key))
        for key in ADMIN_MATCH_FILTER_LABELS
    }

    participants = Participant.query.order_by(Participant.name).all()

    prediction_counts = {
        match_id: count
        for match_id, count in db.session.query(
            Prediction.match_id,
            db.func.count(Prediction.id)
        ).group_by(Prediction.match_id).all()
    }

    return render_template(
        'admin.html',
        t=t,
        matches=matches,
        participants=participants,
        code=code,
        stage_labels=STAGE_LABELS,
        selected_filter=selected_filter,
        match_filter_labels=ADMIN_MATCH_FILTER_LABELS,
        match_counts=match_counts,
        locked=match_locked,
        prediction_counts=prediction_counts,
        champion_test_teams=champion_eligible_teams(t.id),
        champion_preview_teams=champion_preview_teams(t.id),
        champion_team_records=champion_team_records(t.id),
        champion_team_counts=champion_team_counts(t.id),
        champion_team_list_exists=champion_team_list_exists(t.id)
    )

@app.cli.command('init-db')
def init_db():
    print('تم تعطيل init-db لحماية قاعدة البيانات. لا يتم حذف أي بيانات.')


if __name__ == "__main__":
    with app.app_context():
        ensure_database_ready()

    app.run(host="0.0.0.0", port=5000)
