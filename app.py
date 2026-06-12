from flask import Flask, render_template, request, redirect, url_for, abort, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from zoneinfo import ZoneInfo
import secrets

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-this-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///predictions.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

KUWAIT_TZ = ZoneInfo("Asia/Kuwait")

PARTICIPANT_NAMES = [
    'بو براك','بو ضاري','بو صقر','حمني','الحميدي','الخالدي','شافعي','الرشود',
    'العربيد','العومي','عيسى','الفزيع','فواز','القعود','ناصر','الوهيب'
]

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

    matches = Match.query.filter_by(tournament_id=t.id).order_by(Match.start_time).all()
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

        if action == 'predict':
            match = Match.query.get_or_404(int(request.form['match_id']))

            if match_locked(match):
                flash('تم إغلاق التوقع لهذه المباراة.')
                return redirect(url_for('participant_page', token=token))

            hs = int(request.form.get('home_score', 0))
            aw = int(request.form.get('away_score', 0))
            is_double = request.form.get('is_double') == 'on'

            if is_double and match.stage not in KNOCKOUT_STAGES:
                is_double = False

            if is_double and current_double_used(p.id, t.id, match.stage, match.id):
                flash('لا يمكن اختيار أكثر من مباراة مضاعفة واحدة في نفس الدور.')
                return redirect(url_for('participant_page', token=token))

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

        return redirect(url_for('participant_page', token=token))

    teams = sorted({m.home_team for m in matches} | {m.away_team for m in matches})

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
        knockout=KNOCKOUT_STAGES
    )


@app.route('/rules')
def rules():
    t = tournament()
    return render_template('rules.html', t=t)


@app.route('/leaderboard')
def leaderboard():
    t = tournament()
    participants = Participant.query.order_by(Participant.name).all()

    rows = []

    for p in participants:
        preds = Prediction.query.filter_by(participant_id=p.id).all()

        pts = 0
        exact = 0

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

        rows.append({
            'name': p.name,
            'points': pts,
            'exact': exact
        })

    rows.sort(key=lambda r: (r['points'], r['exact']), reverse=True)

    return render_template('leaderboard.html', rows=rows, t=t)


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

        elif action == 'champion_deadline':
            t.champion_pick_deadline = datetime.strptime(
                request.form['deadline'],
                '%Y-%m-%dT%H:%M'
            )

            db.session.commit()

            flash('تم حفظ موعد إغلاق توقع البطل.')

        return redirect(url_for('admin', code=code))

    matches = Match.query.filter_by(tournament_id=t.id).order_by(Match.start_time).all()
    participants = Participant.query.order_by(Participant.name).all()

    return render_template(
        'admin.html',
        t=t,
        matches=matches,
        participants=participants,
        code=code,
        stage_labels=STAGE_LABELS
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
                token=secrets.token_urlsafe(8)
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
                    token=secrets.token_urlsafe(12)
                )
                db.session.add(participant)

            db.session.commit()

    app.run(host="0.0.0.0", port=5000)
