from flask import Flask, render_template, request, redirect, url_for, abort, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import secrets
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-this-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(     'DATABASE_URL',     'sqlite:///predictions.db' )
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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


KNOCKOUT_STAGES = ['round32', 'round16', 'quarter', 'semi', 'final']

STAGE_LABELS = {
    'group': 'المجموعات',
    'round32': 'دور الـ32',
    'round16': 'دور الـ16',
    'quarter': 'ربع النهائي',
    'semi': 'نصف النهائي',
    'final': 'النهائي'
}

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

    replacements = {
        'أ': 'ا',
        'إ': 'ا',
        'آ': 'ا',
        'ى': 'ي',
        'ة': 'ه'
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return ' '.join(text.split())


TEAM_FLAG_CODES = {
    normalize_team_key(name): code
    for name, code in RAW_TEAM_FLAG_CODES.items()
}


def team_flag_code(team_name):
    return TEAM_FLAG_CODES.get(normalize_team_key(team_name))


@app.context_processor
def inject_team_helpers():
    return {
        'team_flag_code': team_flag_code
    }


MATCH_FILTER_LABELS = {
    'open': 'المفتوحة',
    'all': 'كل المباريات'
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
        return [
            m for m in matches
            if not match_locked(m)
        ]

    if selected_filter == 'today':
        return [
            m for m in matches
            if today_start <= m.start_time < tomorrow_start
        ]

    if selected_filter == 'finished':
        return [
            m for m in matches
            if m.home_score is not None and m.away_score is not None
        ]

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

    base = 0

    if pred.home_score == match.home_score and pred.away_score == match.away_score:
        base = 3
    elif winner(pred.home_score, pred.away_score) == winner(match.home_score, match.away_score):
        base = 1

    return base * (2 if pred.is_double else 1)


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


@app.route('/')
def index():
    return redirect(url_for('leaderboard'))


@app.route('/p/<token>', methods=['GET', 'POST'])
def participant_page(token):
    p = participant_by_token(token)
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

                try:
                    hs = int(request.form.get(f'home_score_{match.id}', 0) or 0)
                    aw = int(request.form.get(f'away_score_{match.id}', 0) or 0)
                except ValueError:
                    hs = 0
                    aw = 0

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
                flash('تم حفظ التوقعات.')
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
                team = request.form.get('team_name', '').strip()

                if team:
                    pick = champion or ChampionPick(
                        participant_id=p.id,
                        tournament_id=t.id
                    )

                    pick.team_name = team

                    db.session.add(pick)
                    db.session.commit()

                    flash('تم حفظ توقع البطل.')

        return redirect(url_for(
            'participant_page',
            token=token,
            filter=selected_filter
        ))

    teams = sorted(
        {m.home_team for m in all_matches} |
        {m.away_team for m in all_matches}
    )

    return render_template(
        'participant.html',
        p=p,
        t=t,
        matches=matches,
        predictions=predictions,
        locked=match_locked,
        points_for=points_for,
        champion=champion,
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

@app.route('/rules')
def rules():
    t = tournament()
    return render_template('rules.html', t=t)

@app.route('/matches')
def matches_page():
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

    return render_template(
        'matches.html',
        t=t,
        matches=matches,
        selected_filter=selected_filter,
        match_filter_labels=MATCH_FILTER_LABELS,
        match_counts=match_counts,
        locked=match_locked,
        stage_labels=STAGE_LABELS
    )


@app.route('/today')
def today_page():
    return redirect(url_for('matches_page', filter='open'))

@app.route('/leaderboard')
def leaderboard():
    t = tournament()
    participants = Participant.query.order_by(Participant.name).all()
    prediction_counts = {
    match_id: count
    for match_id, count in db.session.query(
        Prediction.match_id,
        db.func.count(Prediction.id)
    ).group_by(Prediction.match_id).all()
}

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

        if champion_team and champion_pick and champion_pick.team_name == champion_team:
            champion_bonus = 10
            pts += champion_bonus

        rows.append({
            'name': p.name,
            'points': pts,
            'exact': exact,
            'champion_bonus': champion_bonus,
            'starting_bonus': starting_bonus
        })

    rows.sort(
        key=lambda r: (r['points'], r['exact'], r['champion_bonus']),
        reverse=True
    )

    return render_template('leaderboard.html', rows=rows, t=t)


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

        for m in completed_matches:
            pred = preds_by_match.get(m.id)

            if not pred:
                continue

            match_points += points_for(pred, m)

            if pred.home_score == m.home_score and pred.away_score == m.away_score:
                exact += 1

        missed_locked = sum(
            1 for m in locked_matches
            if m.id not in preds_by_match
        )

        player_rows.append({
            'name': p.name,
            'exact': exact,
            'predictions_count': len(preds_by_match),
            'missed_locked': missed_locked,
            'match_points': match_points
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
        player_rows,
        key=lambda r: r['missed_locked'],
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

    top_point_matches = sorted(
        match_rows,
        key=lambda r: (r['total_points'], r['exact_count']),
        reverse=True
    )[:5]

    return render_template(
        'stats.html',
        t=t,
        total_participants=len(participants),
        total_matches=len(all_matches),
        completed_matches_count=len(completed_matches),
        locked_matches_count=len(locked_matches),
        total_predictions=total_predictions,
        top_exact=top_exact,
        top_participation=top_participation,
        most_missed=most_missed,
        top_point_matches=top_point_matches,
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

            flash('تمت إضافة المباراة.')

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

            db.session.delete(m)
            db.session.commit()

            flash('تم حذف المباراة.')

        elif action == 'champion_deadline':
            t.champion_pick_deadline = datetime.strptime(
                request.form['deadline'],
                '%Y-%m-%dT%H:%M'
            )

            db.session.commit()

            flash('تم حفظ موعد إغلاق توقع البطل.')

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
        prediction_counts=prediction_counts
    )
@app.cli.command('init-db')
def init_db():
    db.drop_all()
    db.create_all()

    t = Tournament(name='كأس العالم 2026')
    db.session.add(t)
    db.session.flush()

    for name in PARTICIPANT_NAMES:
        db.session.add(
            Participant(
                name=name,
                token=PARTICIPANT_TOKENS[name]
            )
        )

    db.session.commit()

    print('Database initialized. Admin:', f'/admin/{ADMIN_CODE}')

    for p in Participant.query.order_by(Participant.name).all():
        print(p.name, f'/p/{p.token}')


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

        if Tournament.query.first() is None:
            new_tournament = Tournament(name="كأس العالم 2026")
            db.session.add(new_tournament)
            db.session.commit()

        if Participant.query.count() == 0:
            for name in PARTICIPANT_NAMES:
                participant = Participant(
                    name=name,
                    token=PARTICIPANT_TOKENS[name]
                )
                db.session.add(participant)

            db.session.commit()

    app.run(host="0.0.0.0", port=5000)
