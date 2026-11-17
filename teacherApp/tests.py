from django.test import TestCase

from teacherApp.sanitizers import sanitize_subject_note_html


class SubjectNoteSanitizerTests(TestCase):
    def test_removes_scripts_and_event_handlers(self):
        html = '<p onclick="alert(1)">Hello<script>alert(1)</script><strong>World</strong></p>'

        cleaned = sanitize_subject_note_html(html)

        self.assertIn('<p>Hello<strong>World</strong></p>', cleaned)
        self.assertNotIn('script', cleaned.lower())
        self.assertNotIn('onclick', cleaned.lower())

    def test_rejects_unsafe_links_and_css(self):
        html = (
            '<a href="javascript:alert(1)" target="_blank">Bad</a>'
            '<span style="color: red; background-image: url(javascript:alert(1)); text-align: center">Ok</span>'
        )

        cleaned = sanitize_subject_note_html(html)

        self.assertNotIn('javascript:', cleaned.lower())
        self.assertNotIn('background-image', cleaned.lower())
        self.assertIn('style="color: red; text-align: center"', cleaned)
