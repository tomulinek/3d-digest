"""Testy generátoru. Bez sítě, bez .env, bez skutečného Gemini i GitHubu."""
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import generate_news_json_github as gen  # noqa: E402

NOW = datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc)


def art(url, published='2026-11-04T10:00:00+00:00', image='https://img/x.jpg', **extra):
    return {'url': url, 'published': published, 'image': image, 'title': 't', 'source': 's', **extra}


def summary(**changes):
    data = {
        'is_relevant': True, 'title_cs': 'Nadpis', 'title_en': 'Title',
        'summary_bullets_cs': ['a'], 'summary_bullets_en': ['a'],
        'tags': ['Hardware'], 'region_cs': '🌐 Svět', 'region_en': '🌐 Worldwide',
    }
    return gen.ArticleSummary(**{**data, **changes})


class MemoryStorage:
    def __init__(self, files=None, conflicts=0):
        self.files = {k: json.dumps(v) for k, v in (files or {}).items()}
        self.conflicts = conflicts
        self.writes = []

    def read(self, path):
        return (json.loads(self.files[path]), 'sha') if path in self.files else (None, None)

    def write(self, path, data, message, sha):
        if self.conflicts:
            self.conflicts -= 1
            # Mezitím zapsal někdo jiný
            current = json.loads(self.files.get(path, '[]'))
            self.files[path] = json.dumps(current + [art('https://x/soubezny')])
            raise gen.ConflictError(path)
        self.writes.append(path)
        self.files[path] = json.dumps(data)

    def urls(self, path):
        return [a['url'] for a in json.loads(self.files[path])]


class FakeEnricher:
    model = 'fake'

    def __init__(self, answers=None):
        self.usage = {'calls': 0, 'input': 0, 'output': 0, 'thinking': 0}
        self.answers = answers or {}
        self.seen = []

    def summarize(self, article, language, always_include=False):
        self.seen.append(article['url'])
        answer = self.answers.get(article['url'], summary())
        if isinstance(answer, Exception):
            raise answer
        return answer


class Response:
    def __init__(self, status=200, body=b'', headers=None, url='https://src/', data=None):
        self.status_code = status
        self.content = body
        self.text = body.decode('utf-8')
        self.headers = headers or {'Content-Type': 'text/html; charset=utf-8'}
        self.url = url
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise gen.requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        assert kwargs.get('timeout'), 'každý požadavek musí mít časový limit'
        return self.pages.get(url, Response(404))


def rss(*items, title='Zdroj'):
    body = ''.join(
        f'<item><title>{t}</title><link>{u}</link><pubDate>{d}</pubDate><description>{s}</description></item>'
        for t, u, d, s in items
    )
    text = f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{title}</title>{body}</channel></rss>'
    return Response(body=text.encode('utf-8'), headers={'Content-Type': 'application/rss+xml; charset=utf-8'})


RECENT = 'Wed, 04 Nov 2026 10:00:00 GMT'
OCTOBER = 'Wed, 28 Oct 2026 10:00:00 GMT'
OLD = 'Thu, 01 Oct 2026 10:00:00 GMT'


@pytest.fixture
def one_feed(monkeypatch):
    monkeypatch.setattr(gen, 'FEEDS', (gen.Feed('https://feed/'),))


# --- adresy, měsíce, deduplikace ---

def test_url_key_ignoruje_mereni_ale_ne_rozlisujici_parametry():
    assert gen.url_key('https://a/x?utm_source=rss&id=2#top') == 'https://a/x?id=2'
    assert gen.url_key('https://a/?p=1') != gen.url_key('https://a/?p=2')
    assert gen.url_key(None) == ''


def test_mesice_pres_prelom_roku():
    start, end = datetime(2026, 12, 20, tzinfo=timezone.utc), datetime(2027, 1, 3, tzinfo=timezone.utc)
    assert gen.months_between(start, end) == ['2027_01', '2026_12']
    assert gen.months_between(end, end) == ['2027_01']


def test_deduplikace_uprednostni_obrazek_a_radi_od_nejnovejsich():
    result = gen.deduplicate([
        art('https://a/1', image=''), art('https://a/1?utm_source=x', image='https://i/1.jpg'),
        art('https://a/2', published='2026-11-05T00:00:00+00:00'), {'title': 'bez adresy'},
    ])
    assert [a['url'] for a in result] == ['https://a/2', 'https://a/1?utm_source=x']


@pytest.mark.parametrize('url,ok', [
    ('https://a.cz/x', True), ('http://a.cz', True), ('javascript:alert(1)', False),
    ('ftp://a', False), ('', False), (None, False),
])
def test_is_web_url(url, ok):
    assert gen.is_web_url(url) is ok


# --- obrázky ---

def test_obrazek_http_se_prevede_na_https_a_relativni_na_absolutni():
    assert gen.normalize_image('http://a/x.jpg') == 'https://a/x.jpg'
    assert gen.normalize_image('/x.jpg', 'https://a/b/c') == 'https://a/x.jpg'


def test_prazdny_obrazek_nevrati_adresu_clanku():
    assert gen.normalize_image('', 'https://a/clanek') == ''
    assert gen.normalize_image(None, 'https://a/clanek') == ''


def test_og_image_v_obou_poradich_atributu():
    for tag in ('<meta property="og:image" content="/a.jpg">', '<meta content="/a.jpg" property="og:image">'):
        page = gen.PageMeta()
        page.feed(tag)
        assert gen.page_image(page, 'https://s/') == 'https://s/a.jpg'


def test_obrazek_z_media_thumbnail_se_pouzije_i_kdyz_ma_polozka_odkazy():
    entry = {'links': [{'type': 'text/html', 'href': 'https://a/'}], 'media_thumbnail': [{'url': 'https://a/t.jpg'}]}
    assert gen.image_from_entry(entry, 'https://a/') == 'https://a/t.jpg'


def test_zvuk_a_video_nejsou_obrazek():
    entry = {'enclosures': [{'type': 'audio/mpeg', 'href': 'https://a/p.mp3'}],
             'media_content': [{'type': 'video/mp4', 'url': 'https://a/v.mp4'}]}
    assert gen.image_from_entry(entry, 'https://a/') == ''


# --- RSS ---

def test_feed_vynecha_stare_bez_data_a_nebezpecne_odkazy(one_feed):
    page = rss(('nový', 'https://a/1', RECENT, 's'), ('starý', 'https://a/2', OLD, 's'),
               ('bez data', 'https://a/3', '', 's'), ('zlý', 'javascript:alert(1)', RECENT, 's'))
    articles = gen.fetch_feed(gen.FEEDS[0], FakeSession({'https://feed/': page}), NOW)
    assert [a['url'] for a in articles] == ['https://a/1']
    assert articles[0]['source'] == 'Zdroj'
    assert articles[0]['published'] == '2026-11-04T10:00:00+00:00'


def test_cesky_text_z_rss_prezije():
    page = rss(('Příliš žluťoučký kůň', 'https://a/1', RECENT, 's'), title='Blog ADITIVNÍ.cz')
    articles = gen.fetch_feed(gen.Feed('https://feed/'), FakeSession({'https://feed/': page}), NOW)
    assert (articles[0]['title'], articles[0]['source']) == ('Příliš žluťoučký kůň', 'Blog ADITIVNÍ.cz')


def test_selhani_jednoho_zdroje_nezastavi_ostatni(monkeypatch):
    monkeypatch.setattr(gen, 'FEEDS', (gen.Feed('https://mrtvy/'), gen.Feed('https://feed/')))
    report = gen.RunReport()
    session = FakeSession({'https://feed/': rss(('a', 'https://a/1', RECENT, 's'))})
    pending = gen.collect_new_articles(session, set(), NOW, report)
    assert [a['url'] for _, a in pending] == ['https://a/1']
    assert report.feeds_failed == ['https://mrtvy/'] and report.exit_code == 0


def test_kdyz_selzou_vsechny_zdroje_beh_hlasi_chybu(one_feed):
    report = gen.run(MemoryStorage(), FakeEnricher(), FakeSession({}), NOW)
    assert report.exit_code == 1


# --- Gemini ---

class FakeModels:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def gemini(response):
    client = type('Client', (), {})()
    client.models = FakeModels(response)
    return gen.GeminiEnricher(client, 'model-x'), client.models


def sdk_response(*parts, finish='STOP'):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(
            finish_reason=finish,
            content=types.Content(role='model', parts=[types.Part(**p) for p in parts]),
        )],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=5, thoughts_token_count=7),
    )


def test_volani_gemini_ma_schema_thinking_a_zadnou_teplotu():
    enricher, models = gemini(sdk_response({'text': summary().model_dump_json()}))
    enricher.summarize(art('https://a/1', summary='<p>Ahoj&nbsp;<b>světe</b></p>'), 'cs')
    config = models.kwargs['config']
    assert models.kwargs['model'] == 'model-x'
    assert config.temperature is None
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert config.thinking_config.thinking_budget is None
    assert config.response_mime_type == 'application/json'
    assert set(config.response_json_schema['required']) == set(gen.ArticleSummary.model_fields)
    assert 'Souhrn: Ahoj světe' in models.kwargs['contents']
    assert enricher.usage == {'calls': 1, 'input': 10, 'output': 5, 'thinking': 7}


def test_myslenky_modelu_se_do_odpovedi_nepletou():
    enricher, _ = gemini(sdk_response({'text': 'přemýšlím', 'thought': True},
                                      {'text': summary().model_dump_json()}))
    assert enricher.summarize(art('https://a/1'), 'en').title_cs == 'Nadpis'


@pytest.mark.parametrize('response', [
    sdk_response({'text': 'x', 'thought': True}, finish='MAX_TOKENS'),
    sdk_response({'text': '{"is_relevant": true, "title_cs": "use'}, finish='MAX_TOKENS'),
    sdk_response({'text': '{"is_relevant": true}'}),
    sdk_response({'text': summary(summary_bullets_cs=[' ']).model_dump_json()}),
    sdk_response({'text': summary(title_en='').model_dump_json()}),
    types.GenerateContentResponse(candidates=[]),
])
def test_neuplna_odpoved_je_chyba_ne_prazdny_clanek(response):
    enricher, _ = gemini(response)
    with pytest.raises(gen.AIError):
        enricher.summarize(art('https://a/1'), 'en')


def test_chyba_klice_je_fatalni_pretizeni_ne():
    for code, fatal in ((403, True), (404, True), (429, False), (503, False)):
        exc = RuntimeError('x')
        exc.code = code
        enricher, _ = gemini(exc)
        with pytest.raises(gen.AIError) as info:
            enricher.summarize(art('https://a/1'), 'en')
        assert info.value.fatal is fatal


def test_prazdna_promenna_neprebije_vychozi_model(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'x')
    monkeypatch.setenv('GEMINI_MODEL', '')
    monkeypatch.setenv('GEMINI_THINKING_LEVEL', '')
    assert gen.GeminiEnricher.from_env().model == gen.DEFAULT_GEMINI_MODEL
    monkeypatch.setenv('GEMINI_MODEL', 'jiny')
    assert gen.GeminiEnricher.from_env().model == 'jiny'


def test_bez_klice_srozumitelna_chyba(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    with pytest.raises(gen.AIError) as info:
        gen.GeminiEnricher.from_env()
    assert info.value.fatal


# --- běh ---

def test_ulozene_clanky_se_neposilaji_do_ai_ani_kdyz_jsou_v_minulem_mesici(one_feed):
    storage = MemoryStorage({
        'news_2026_10.json': [art('https://a/rijen', '2026-10-28T10:00:00+00:00')],
        'news_2026_11.json': [art('https://a/listopad')],
        'news_index.json': {'months': ['2026_11', '2026_10']},
    })
    enricher = FakeEnricher()
    session = FakeSession({'https://feed/': rss(
        ('r', 'https://a/rijen?utm_source=rss', OCTOBER, 's'), ('l', 'https://a/listopad', RECENT, 's'))})
    gen.run(storage, enricher, session, NOW)
    assert enricher.seen == [] and storage.writes == []


def test_clanek_se_ulozi_do_mesice_vydani(one_feed):
    storage = MemoryStorage({
        'news_2026_10.json': [], 'news_2026_11.json': [art('https://a/l')],
        'news_index.json': {'months': ['2026_11', '2026_10'], 'total_articles': 1},
    })
    session = FakeSession({'https://feed/': rss(
        ('r', 'https://a/rijen', OCTOBER, 's'), ('n', 'https://a/novy', RECENT, 's'))})
    report = gen.run(storage, FakeEnricher(), session, NOW)
    assert storage.urls('news_2026_10.json') == ['https://a/rijen']
    assert sorted(storage.urls('news_2026_11.json')) == ['https://a/l', 'https://a/novy']
    index = storage.read('news_index.json')[0]
    assert index['counts'] == {'2026_11': 2, '2026_10': 1} and index['total_articles'] == 3
    assert report.new == 2 and report.exit_code == 0


def test_nerelevantni_se_vyradi_ale_sponzorsky_zdroj_ne(monkeypatch):
    monkeypatch.setattr(gen, 'FEEDS', (
        gen.Feed('https://feed/'), gen.Feed('https://sponzor/', 'cs', True, ('ADITIVNI.cz',))))
    off = summary(is_relevant=False)
    storage = MemoryStorage()
    session = FakeSession({
        'https://feed/': rss(('x', 'https://a/mimo', RECENT, 's')),
        'https://sponzor/': rss(('y', 'https://b/sponzor', RECENT, 's')),
    })
    report = gen.run(storage, FakeEnricher({'https://a/mimo': off, 'https://b/sponzor': off}), session, NOW)
    stored = storage.read('news_2026_11.json')[0]
    assert [a['url'] for a in stored] == ['https://b/sponzor']
    assert stored[0]['tags'] == ['Hardware', 'ADITIVNI.cz']
    assert report.rejected == 1


def test_selhany_clanek_se_neulozi_a_pristi_beh_ho_zkusi_znovu(one_feed):
    storage = MemoryStorage()
    session = FakeSession({'https://feed/': rss(
        ('a', 'https://a/1', RECENT, 's'), ('b', 'https://a/2', RECENT, 's'))})
    first = gen.run(storage, FakeEnricher({'https://a/1': gen.AIError('x')}), session, NOW)
    assert storage.urls('news_2026_11.json') == ['https://a/2'] and first.failed == 1
    second = FakeEnricher()
    gen.run(storage, second, session, NOW)
    assert second.seen == ['https://a/1']
    assert sorted(storage.urls('news_2026_11.json')) == ['https://a/1', 'https://a/2']


def test_opakovane_selhani_ai_zastavi_beh_ale_ulozi_hotove(one_feed):
    items = [(str(i), f'https://a/{i}', RECENT, 's') for i in range(8)]
    answers = {f'https://a/{i}': gen.AIError('429') for i in range(1, 8)}
    enricher, storage = FakeEnricher(answers), MemoryStorage()
    report = gen.run(storage, enricher, FakeSession({'https://feed/': rss(*items)}), NOW)
    assert len(enricher.seen) == 1 + gen.MAX_CONSECUTIVE_AI_FAILURES
    assert storage.urls('news_2026_11.json') == ['https://a/0']
    assert report.aborted and report.exit_code == 1


def test_necitelny_archiv_ukonci_beh_pred_ai(one_feed):
    class Broken(MemoryStorage):
        def read(self, path):
            raise gen.requests.HTTPError('500')
    enricher = FakeEnricher()
    with pytest.raises(gen.requests.HTTPError):
        gen.run(Broken(), enricher, FakeSession({'https://feed/': rss(('a', 'https://a/1', RECENT, 's'))}), NOW)
    assert enricher.seen == []


def test_bez_zmeny_se_nic_nezapise(one_feed):
    storage = MemoryStorage({'news_2026_11.json': [art('https://a/1', image='')],
                             'news_index.json': {'months': ['2026_11']}})
    gen.run(storage, FakeEnricher(), FakeSession({'https://feed/': rss(('a', 'https://a/1', RECENT, 's'))}), NOW)
    assert storage.writes == []


def test_chybejici_obrazek_se_doplni(one_feed):
    storage = MemoryStorage({'news_2026_11.json': [art('https://a/1', image='')],
                             'news_index.json': {'months': ['2026_11']}})
    session = FakeSession({
        'https://feed/': rss(('a', 'https://a/1', RECENT, 's')),
        'https://a/1': Response(body=b'<meta property="og:image" content="/o.jpg">', url='https://a/1')})
    gen.run(storage, FakeEnricher(), session, NOW)
    assert storage.read('news_2026_11.json')[0][0]['image'] == 'https://a/o.jpg'


def test_soubezny_zapis_nic_neztrati():
    storage = MemoryStorage({'news_2026_11.json': [art('https://a/1')], 'news_index.json': {'months': ['2026_11']}},
                            conflicts=1)
    gen.store_articles(storage, [art('https://a/2')], NOW)
    assert sorted(storage.urls('news_2026_11.json')) == ['https://a/1', 'https://a/2', 'https://x/soubezny']


def test_chyba_cteni_indexu_index_neprepise():
    class Broken(MemoryStorage):
        def read(self, path):
            if path == gen.INDEX_FILE:
                raise gen.requests.HTTPError('500')
            return super().read(path)
    storage = Broken()
    with pytest.raises(gen.requests.HTTPError):
        gen.store_articles(storage, [art('https://a/1')], NOW)
    assert gen.INDEX_FILE not in storage.writes


# --- čištění archivu ---

def test_cisteni_presune_a_nic_neztrati():
    storage = MemoryStorage({
        'news_2026_10.json': [art('https://a/1', '2026-10-30T00:00:00+00:00')],
        'news_2026_11.json': [art('https://a/1', '2026-10-30T00:00:00+00:00'),
                              art('https://a/2', '2026-10-31T00:00:00+00:00'), art('https://a/3')],
        'news_index.json': {'months': ['2026_11', '2026_10'], 'total_articles': 4},
    })
    assert gen.clean_archive(storage, NOW) == (4, 3)
    assert storage.urls('news_2026_10.json') == ['https://a/2', 'https://a/1']
    assert storage.urls('news_2026_11.json') == ['https://a/3']
    assert storage.writes[0] == 'news_2026_10.json', 'nejdřív se zapisuje cíl přesunu'
    assert storage.read('news_index.json')[0]['total_articles'] == 3
    storage.writes.clear()
    gen.clean_archive(storage, NOW)
    assert storage.writes == [], 'druhé čištění už nemá co měnit'


def test_cisteni_se_zastavi_kdyz_soubor_chybi():
    storage = MemoryStorage({'news_index.json': {'months': ['2026_11']}})
    with pytest.raises(RuntimeError):
        gen.clean_archive(storage, NOW)
    assert storage.writes == []


# --- GitHub ---

class GitHubSession:
    def __init__(self, get=None, put_status=200):
        self.get_map, self.put_status, self.put_payload = get or {}, put_status, None

    def get(self, url, **kwargs):
        assert kwargs.get('timeout')
        return self.get_map.get(url.split('/3d-digest/')[1], Response(404))

    def put(self, url, **kwargs):
        assert kwargs.get('timeout')
        self.put_payload = kwargs['json']
        return Response(self.put_status)


def encoded(data):
    return base64.b64encode(json.dumps(data).encode()).decode()


def test_velky_soubor_se_cte_pres_blob_podle_sha():
    session = GitHubSession({
        'contents/news.json': Response(data={'sha': 'abc', 'encoding': 'none', 'content': '', 'size': 2_000_000}),
        'git/blobs/abc': Response(data={'content': encoded([1, 2])}),
    })
    assert gen.GitHubStorage('t', session).read('news.json') == ([1, 2], 'abc')


def test_maly_soubor_chybejici_a_chyba_serveru():
    session = GitHubSession({
        'contents/a.json': Response(data={'sha': 's', 'encoding': 'base64', 'content': encoded({'x': 1})}),
        'contents/c.json': Response(500),
    })
    storage = gen.GitHubStorage('t', session)
    assert storage.read('a.json') == ({'x': 1}, 's')
    assert storage.read('b.json') == (None, None)
    with pytest.raises(gen.requests.HTTPError):
        storage.read('c.json')


def test_zapis_konflikt_a_chyba():
    ok = GitHubSession()
    gen.GitHubStorage('t', ok).write('a.json', ['č'], 'm', 'sha1')
    assert ok.put_payload['sha'] == 'sha1'
    assert json.loads(base64.b64decode(ok.put_payload['content'])) == ['č']
    with pytest.raises(gen.ConflictError):
        gen.GitHubStorage('t', GitHubSession(put_status=409)).write('a.json', [], 'm', 'sha1')
    with pytest.raises(gen.requests.HTTPError):
        gen.GitHubStorage('t', GitHubSession(put_status=403)).write('a.json', [], 'm', 'sha1')


def test_beh_nanecisto_zapisuje_jen_do_slozky(tmp_path):
    session = FakeSession({})
    storage = gen.DryRunStorage(tmp_path / 'out', session)
    storage.write('a.json', [1], 'm', None)
    assert storage.read('a.json')[0] == [1] and session.requested == []


# --- ruční přidání ---

def test_rucni_clanek_obchazi_filtr_a_neduplikuje():
    url = 'https://novinky.example/clanek'
    page = Response(body='<title>Nadpis</title><meta property="og:image" content="/o.jpg">'.encode(), url=url)
    storage, enricher = MemoryStorage(), FakeEnricher({url: summary(is_relevant=False)})
    assert gen.add_manual_article(storage, enricher, FakeSession({url: page}), url, NOW) is True
    stored = storage.read('news_2026_11.json')[0]
    assert [a['url'] for a in stored] == [url] and stored[0]['image'] == 'https://novinky.example/o.jpg'
    assert gen.add_manual_article(storage, enricher, FakeSession({url: page}), url + '?utm_source=x', NOW) is False
    with pytest.raises(ValueError):
        gen.add_manual_article(storage, enricher, FakeSession({}), 'javascript:alert(1)', NOW)
