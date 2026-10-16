"""User options (2.8.3): stated keys over the saved defaults over HWP_* over skill defaults, frozen per job,
and the switches they drive: figures=crop, figure_check, model tiers, light review, repair rounds."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
import restoration_job as job
import restoration_options as options
import restoration_single as single

MD = '''## left
### q1
1. 그림과 같이 $\\overline{AB}=3$일 때, $x$의 값은?

![](figure:q1-figure-1)

::: choices 2
① $1$ | ② $2$
:::
'''


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.prefs = Path(self.tmp.name) / 'preferences.json'
        patcher = mock.patch.dict(os.environ, {'HWP_PREFERENCES': str(self.prefs)}); patcher.start(); self.addCleanup(patcher.stop)
        for key in ('HWP_MAX_REREVIEWS', 'HWP_PAGE_IMAGE_MODE', 'HWP_SOURCE_TILES', 'HWP_REVIEW_TILES'):
            os.environ.pop(key, None)

    def test_nothing_stated_is_todays_behaviour(self):
        stated, effective, warnings = options.resolve({})
        self.assertEqual((stated, warnings), ({}, []))
        self.assertEqual(effective, options.DEFAULTS)
        self.assertEqual((effective['figures'], effective['review'], effective['repair_rounds'], effective['figure_fix_limit']),
                         ('restore', 'full', 1, 2))

    def test_request_over_preferences_over_environment(self):
        self.prefs.write_text(json.dumps({'review': 'answers', 'repair_rounds': 0, 'figures': 'crop'}), encoding='utf-8')
        with mock.patch.dict(os.environ, {'HWP_MAX_REREVIEWS': '1', 'HWP_PAGE_IMAGE_MODE': 'gray16'}):
            stated, effective, _ = options.resolve({'figures': 'off'})
        self.assertEqual(effective['figures'], 'off')          # request
        self.assertEqual(effective['review'], 'answers')       # preferences
        self.assertEqual(effective['repair_rounds'], 0)        # preferences over HWP_MAX_REREVIEWS
        self.assertEqual(effective['image_mode'], 'gray16')    # environment, nothing stated
        self.assertNotIn('image_mode', stated)                 # environment stays live, not frozen

    def test_preset_then_stated_keys_win(self):
        _, effective, _ = options.resolve({'preset': 'fast', 'review': 'full'})
        self.assertEqual((effective['figures'], effective['review'], effective['repair_rounds']), ('crop', 'full', 0))

    def test_bad_values_are_ignored_with_a_warning(self):
        stated, _, warnings = options.resolve({'figures': 'paint', 'repair_rounds': 9, 'nonsense': 1, 'review': 'OFF'})
        self.assertEqual(stated, {'review': 'off'})
        self.assertEqual(len(warnings), 3)

    def test_light_reader_warns(self):
        _, _, warnings = options.resolve({'reader_model': 'light'})
        self.assertIn('Flash-Lite', warnings[0])
        self.assertEqual(options.model_for('light')['antigravity'], 'flash_lite')
        self.assertEqual(options.model_for('standard')['antigravity'], 'flash')

    def test_save_merges_into_the_file(self):
        self.prefs.write_text(json.dumps({'review': 'answers'}), encoding='utf-8')
        options.save_preferences({'figures': 'crop'})
        self.assertEqual(json.loads(self.prefs.read_text(encoding='utf-8')), {'review': 'answers', 'figures': 'crop'})

    def test_old_jobs_read_as_their_include_keys(self):
        self.assertEqual(options.job_options({'include_figures': False, 'include_answers': True})['figures'], 'off')
        self.assertEqual(options.job_options({})['answers'], 'off')  # the read default of jobs without the key


class JobTests(unittest.TestCase):
    def setUp(self):
        import fitz
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'job'; self.source = Path(self.tmp.name) / 'source.pdf'
        self.prefs = Path(self.tmp.name) / 'preferences.json'
        patcher = mock.patch.dict(os.environ, {'HWP_PREFERENCES': str(self.prefs)}); patcher.start(); self.addCleanup(patcher.stop)
        with fitz.open() as doc:
            page = doc.new_page()
            page.draw_rect(fitz.Rect(100, 100, 200, 180), color=(0, 0, 0), width=2)  # something to crop
            doc.save(self.source)

    def call(self, action, **kwargs):
        return single.dispatch(action, {'job': str(self.root), **kwargs})

    def prepare(self, **kwargs):
        result = self.call('prepare', source=str(self.source), question_pages=[1], **kwargs)
        self.assertEqual(result['status'], 'prepared', result)
        self.call('assign', page=1, worker_id='producer', evidence='Actual fixture worker response: producer')
        return result

    def test_saved_defaults_apply_when_options_are_present(self):
        self.prefs.write_text(json.dumps({'figures': 'off', 'answers': 'off'}), encoding='utf-8')
        result = self.prepare(options={})
        self.assertEqual(result['effective_options']['figures'], 'off')
        self.assertFalse(job._manifest(self.root)['include_figures'])
        self.assertIn('include_figures=false', (self.root / 'workers/page-0001/task.md').read_text(encoding='utf-8'))

    def test_engine_callers_without_options_keep_old_defaults(self):
        self.prefs.write_text(json.dumps({'figures': 'off', 'answers': 'on'}), encoding='utf-8')
        self.prepare()
        m = job._manifest(self.root)
        self.assertEqual((m['include_figures'], m['include_answers']), (True, False))
        self.assertNotIn('options', m)

    def test_save_as_default(self):
        result = self.prepare(options={'review': 'off'}, save_as_default=True)
        self.assertEqual(json.loads(self.prefs.read_text(encoding='utf-8')), {'review': 'off'})
        self.assertTrue(any('saved as default' in w for w in result['warnings']))

    def test_options_are_frozen_once_producers_are_bound(self):
        self.prepare(options={'review': 'answers'})
        self.call('prepare', source=str(self.source), question_pages=[1], options={'review': 'off'})
        self.assertEqual(single.job_options(self.root)['review'], 'answers')

    def test_model_tiers_reach_spawn_requests(self):
        result = self.prepare(options={'reader_model': 'standard'})
        self.assertEqual(result['spawn_requests'][0]['model']['antigravity'], 'flash')

    def test_repair_rounds_option(self):
        self.prepare(options={'repair_rounds': 0, 'answers': 'off'})
        self.assertEqual(single.max_failed_verdicts(self.root), 1)

    def test_crop_job_pastes_the_source_without_a_review(self):
        self.prepare(options={'figures': 'crop', 'answers': 'off'})
        result = self.call('submit_reading', page=1, markdown=MD)
        self.assertEqual(result['status'], 'ready_for_figures', result)
        self.assertIn('잘라 넣기', result['figure_rules'])
        folder = self.root / 'workers/page-0001'
        (folder / 'q1-figure-1.tex').write_text('% width_mm=30 source_bbox_px=250,250,320,240\n', encoding='utf-8')
        result = self.call('render_figures', page=1)
        self.assertEqual(result['status'], 'accepted', result)
        self.assertIn('q1-figure-1', result['crop_images'])
        [page] = job.assemble(self.root)
        figure = page['questions'][0]['content']
        render = json.loads(Path(str(figure).split("'render': {'path': '")[1].split("'")[0]).read_text(encoding='utf-8'))
        self.assertEqual(render['renderer'], 'crop')

    def test_crop_box_fixes_stop_at_the_fix_limit(self):
        self.prepare(options={'figures': 'crop', 'answers': 'off', 'figure_fix_limit': 1})
        self.call('submit_reading', page=1, markdown=MD)
        tex = self.root / 'workers/page-0001/q1-figure-1.tex'
        boxes = ([250, 250, 320, 240], [260, 260, 300, 220], [270, 270, 280, 200])
        results = []
        for box in boxes:
            tex.write_text('%% width_mm=30 source_bbox_px=%d,%d,%d,%d\n' % tuple(box), encoding='utf-8')
            results.append(self.call('render_figures', page=1))
        self.assertTrue(all(r['status'] == 'accepted' for r in results), results)
        self.assertIn('연필 자국', results[0]['crop_note'])
        self.assertNotIn('crop_fix_limit_reached', results[0])
        self.assertEqual(results[1]['crop_fix_limit_reached'], ['q1-figure-1'])
        self.assertEqual(results[2]['crop_fix_limit_reached'], ['q1-figure-1'])
        state = json.loads((self.root / 'mcp/state.json').read_text(encoding='utf-8'))
        self.assertEqual(state['figure_edits']['1/q1-figure-1']['count'], 1)
        self.assertEqual(state['figure_sources']['1']['q1-figure-1'], boxes[1])  # the third box was not used

    def test_crop_edges_snap_to_the_nearest_blank_line(self):
        from PIL import Image, ImageDraw
        from restoration_figure_crop import snap_box
        page = Image.new('RGB', (400, 400), 'white'); draw = ImageDraw.Draw(page)
        draw.rectangle((100, 90, 120, 110), fill='black')    # a label the top edge cuts through
        draw.rectangle((100, 120, 300, 280), outline='black', width=3)  # the figure
        draw.rectangle((100, 296, 140, 320), fill='black')   # choice text the bottom edge just touches
        draw.line((60, 150, 60, 380), fill=(150, 150, 150), width=2)  # a pencil arc across the bottom edge
        self.assertEqual(snap_box(page, [50, 100, 300, 200]), [50, 89, 300, 207])

    def test_rough_box_is_fitted_to_the_figure_and_its_labels(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest('OpenCV not installed')
        from PIL import Image, ImageDraw
        import restoration_figure_snap as snap
        page = Image.new('RGB', (1654, 2339), 'white'); draw = ImageDraw.Draw(page)
        for x in range(300, 1000, 30):  # a body text row above the figure
            draw.rectangle((x, 400, x + 18, 426), fill='black')
        draw.polygon([(500, 500), (380, 760), (720, 760)], outline='black', width=4)
        for box in ((490, 466, 508, 492), (350, 770, 368, 796), (725, 770, 743, 796)):  # labels A, B, C
            draw.rectangle(box, fill='black')
        draw.line((150, 600, 260, 900), fill=(170, 170, 170), width=3)  # pencil
        path = Path(self.tmp.name) / 'page.png'; page.save(path)
        x, y, w, h = snap.fit_boxes(path, {'f': [400, 480, 300, 270]})['f']
        self.assertTrue(x <= 350 and x + w >= 743 and y + h >= 796, (x, y, w, h))  # every label is in
        self.assertTrue(430 <= y <= 466, (x, y, w, h))  # the text row is not

    def test_first_crop_box_is_fitted_and_a_rewritten_one_is_cut_as_written(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest('OpenCV not installed')
        self.prepare(options={'figures': 'crop', 'answers': 'off'})
        self.call('submit_reading', page=1, markdown=MD)
        tex = self.root / 'workers/page-0001/q1-figure-1.tex'
        tex.write_text('% width_mm=30 source_bbox_px=250,250,320,240\n', encoding='utf-8')
        first = self.call('render_figures', page=1)
        tex.write_text('% width_mm=30 source_bbox_px=260,260,310,230\n', encoding='utf-8')
        second = self.call('render_figures', page=1)
        self.assertIn('엔진이 그림 선과 라벨에 맞춰', first['crop_note'])
        self.assertNotIn('엔진이 그림 선과 라벨에 맞춰', second['crop_note'])

    def test_crop_needs_a_source_box(self):
        self.prepare(options={'figures': 'crop', 'answers': 'off'})
        self.call('submit_reading', page=1, markdown=MD)
        (self.root / 'workers/page-0001/q1-figure-1.tex').write_text('% width_mm=30\n', encoding='utf-8')
        result = self.call('render_figures', page=1)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('crop_needs_source_bbox_px', result['message'])

    def test_light_review_skeleton_lists_source_and_text_only(self):
        self.prepare(options={'review': 'answers', 'answers': 'off'})
        report = self.root / 'review/round-01.md'
        task = {'page': 1, 'source_image': 'S.png', 'output_image': 'O.png', 'figure_sheet': 'F.png'}
        single.write_report_skeleton(self.root, report, [task], {'pages': {}})
        text = report.read_text(encoding='utf-8')
        self.assertIn('review=answers', text)
        self.assertIn('S.png', text)
        for absent in ('O.png', 'F.png', '- 선택지 행:'):
            self.assertNotIn(absent, text)


class StartReportTests(unittest.TestCase):
    """The settings are shown to the user before producers start; header fields come from the file name."""

    def setUp(self):
        import fitz
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.prefs = Path(self.tmp.name) / 'preferences.json'
        patcher = mock.patch.dict(os.environ, {'HWP_PREFERENCES': str(self.prefs)}); patcher.start(); self.addCleanup(patcher.stop)
        self.source = Path(self.tmp.name) / '[2024년 기출] 예시고 1-2 중간.pdf'; self.root = Path(self.tmp.name) / 'job'
        with fitz.open() as doc:
            doc.new_page(); doc.save(self.source)

    def prepare(self, **options):
        return single.dispatch('prepare', {'job': str(self.root), 'source': str(self.source), 'question_pages': [1], 'options': options})

    def test_file_names(self):
        cases = {'예시중_2-2_중간.pdf': {'school': '예시중', 'exam_title': '2-2 중간'},
                 '2020학년도_샘플고_2-2_중간_수학Ⅱ.pdf': {'year': '2020', 'school': '샘플고', 'exam_title': '2-2 중간'},
                 '보기중학교 2학년 2학기 기말.pdf': {'school': '보기중학교', 'exam_title': '2-2 기말'},
                 '시험지.pdf': {}}
        for name, expected in cases.items():
            self.assertEqual(options.guess_metadata(name), expected, name)

    def test_summary_is_announced_and_the_run_starts(self):
        result = self.prepare()
        self.assertEqual(result['effective_options']['metadata'], {'year': '2024', 'school': '예시고', 'exam_title': '1-2 중간'})
        self.assertTrue(result['settings_summary'].startswith('**이렇게 작업을 시작합니다.**'))
        self.assertIn('학교 예시고 (파일 이름에서)', result['settings_summary'])
        self.assertIn('전체 검수', result['settings_summary'])
        self.assertFalse(result['confirm_settings'])
        self.assertTrue(result['next_action'].startswith('Announce settings_summary'))
        self.assertIn('without asking for approval, then continue at once', result['next_action'])

    def test_user_words_beat_the_file_name_and_changes_apply_before_assignment(self):
        self.prepare()
        result = self.prepare(metadata={'year': '2023'}, review='off')
        self.assertEqual(result['effective_options']['metadata']['year'], '2023')
        self.assertEqual(result['effective_options']['metadata']['school'], '예시고')
        self.assertIn('검수 안 함 · 검수 노트 쪽 붙임 (이번 요청)', result['settings_summary'])

    def test_confirmation_can_be_turned_on(self):
        result = self.prepare(confirm_settings='on')
        self.assertTrue(result['confirm_settings'])
        self.assertTrue(result['settings_summary'].startswith('**이번 작업 설정**'))
        self.assertTrue(result['next_action'].startswith('First show settings_summary'))

    def test_unknown_header_fields_are_called_out(self):
        self.source = self.source.rename(self.source.with_name('시험지.pdf'))
        summary = self.prepare()['settings_summary']
        self.assertIn('미정인 칸은 `-`로 인쇄됩니다', summary)


class AgentVariantTests(unittest.TestCase):
    def test_variants_differ_from_their_role_only_in_name_and_model(self):
        for role in ('reader', 'reviewer'):
            base = (SKILL / f'agents/hwp-restoration-{role}.md').read_text(encoding='utf-8')
            for tier, model in (('standard', 'flash'), ('light', 'flash_lite')):
                variant = (SKILL / f'agents/hwp-restoration-{role}-{tier}.md').read_text(encoding='utf-8')
                expected = (base.replace(f'name: hwp-restoration-{role}\n', f'name: hwp-restoration-{role}-{tier}\n', 1)
                            .replace('\nmodel: inherit\n', f'\nmodel: {model}\n', 1))
                self.assertEqual(variant, expected, f'{role}-{tier}: regenerate from the role file')

    def test_installed_variant_is_used_for_its_tier(self):
        with tempfile.TemporaryDirectory() as home:
            agents = Path(home) / '.gemini/config/agents'; agents.mkdir(parents=True)
            name = 'hwp-restoration-reader-light'
            (agents / f'{name}.md').write_bytes((SKILL / f'agents/{name}.md').read_bytes())
            with mock.patch.object(Path, 'home', return_value=Path(home)):
                self.assertEqual(single.subagent_type('hwp-restoration-reader', 'light'), name)
                self.assertEqual(single.subagent_type('hwp-restoration-reader', 'standard'), 'self')


if __name__ == '__main__':
    unittest.main()
