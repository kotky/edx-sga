"""
This block defines a Staff Graded Assignment.  Students are shown a rubric
and invited to upload a file which is then graded by staff.
"""
import datetime
import hashlib
import json
import logging
import mimetypes
import os
import pkg_resources
import pytz

from functools import partial

from courseware.models import StudentModule

from django.core.files import File
from django.core.files.storage import default_storage
from django.template.context import Context
from django.template.loader import get_template

from webob.response import Response

from xblock.core import XBlock
from xblock.fields import DateTime, Scope, String, Float
from xblock.fragment import Fragment

log = logging.getLogger(__name__)


class StaffGradedAssignmentXBlock(XBlock):
    """
    This block defines a Staff Graded Assignment.  Students are shown a rubric
    and invited to upload a file which is then graded by staff.
    """
    has_score = True
    icon_class = 'problem'

    display_name = String(
        default='Staff Graded Assignment', scope=Scope.settings,
        help="This name appears in the horizontal navigation at the top of "
             "the page.")

    weight = Float(
        display_name="Problem Weight",
        help=("Defines the number of points each problem is worth. "
              "If the value is not set, the problem is worth the sum of the "
              "option point values."),
        values={"min": 0, "step": .1},
        scope=Scope.settings
    )

    points = Float(
        display_name="Maximum score",
        help=("Maximum grade score given to assignment by staff."),
        values={"min": 0, "step": .1},
        default=100,
        scope=Scope.settings
    )

    score = Float(
        display_name="Grade score",
        default=0,
        help=("Grade score given to assignment by staff."),
        values={"min": 0, "step": .1},
        scope=Scope.user_state
    )

    uploaded_sha1 = String(
        display_name="Upload SHA1",
        scope=Scope.user_state,
        default=None,
        help="sha1 of the file uploaded by the student for this assignment.")

    uploaded_filename = String(
        display_name="Upload file name",
        scope=Scope.user_state,
        default=None,
        help="The name of the file uploaded for this assignment.")

    uploaded_mimetype = String(
        display_name="Mime type of uploaded file",
        scope=Scope.user_state,
        default=None,
        help="The mimetype of the file uploaded for this assignment.")

    uploaded_timestamp = DateTime(
        display_name="Timestamp",
        scope=Scope.user_state,
        default=None,
        help="When the file was uploaded")

    def max_score(self):
        return self.points

    def student_view(self, context=None):
        """
        The primary view of the StaffGradedAssignmentXBlock, shown to students
        when viewing courses.
        """
        template = get_template("staff_graded_assignment/show.html")
        context = {
            "student_state": json.dumps(self.student_state()),
            "id": "_".join(filter(None, self.location))
        }
        if self.show_staff_grading_interface():
            context['is_course_staff'] = True
        fragment = Fragment(template.render(Context(context)))
        fragment.add_css(_resource("static/css/edx_sga.css"))
        fragment.add_javascript(_resource("static/js/src/edx_sga.js"))
        fragment.initialize_js('StaffGradedAssignmentXBlock')
        return fragment

    def student_state(self):
        """
        Returns a JSON serializable representation of student's state for
        rendering in client view.
        """
        if self.uploaded_sha1:
            uploaded = {
                "filename": self.uploaded_filename,
            }
        else:
            uploaded = None

        return {
            "uploaded": uploaded
        }

    def staff_grading_data(self):
        def get_student_data(module):
            state = json.loads(module.state)
            return {
                'module_id': module.id,
                'username': module.student.username,
                'fullname': module.student.profile.name,
                'filename': state.get("uploaded_filename"),
                'timestamp': state.get("uploaded_timestamp"),
                'score': state.get("score")
            }

        query = StudentModule.objects.filter(
            course_id=self.xmodule_runtime.course_id,
            module_state_key=self.location.url())

        return {
            'assignments': [get_student_data(module) for module in query],
        }

    def studio_view(self, context=None):
        try:
            cls = type(self)
            def none_to_empty(x):
                return x if x is not None else ''
            edit_fields = (
                (field, none_to_empty(getattr(self, field.name)), validator)
                for field, validator in (
                    (cls.display_name, 'string'),
                    (cls.points, 'number'),
                    (cls.weight, 'number')))

            template = get_template("staff_graded_assignment/edit.html")
            fragment = Fragment(template.render(Context({
                "fields": edit_fields
            })))
            fragment.add_javascript(_resource("static/js/src/studio.js"))
            fragment.initialize_js('StaffGradedAssignmentXBlock')
            return fragment
        except:
            log.error("Don't swallow my exceptions", exc_info=True)
            raise

    @XBlock.json_handler
    def save_sga(self, data, suffix=''):
        for name in ('display_name', 'points', 'weight'):
            setattr(self, name, data.get(name, getattr(self, name)))

    @XBlock.handler
    def upload_assignment(self, request, suffix=''):
        upload = request.params['assignment']
        self.uploaded_sha1 = _get_sha1(upload.file)
        self.uploaded_filename = upload.file.name
        self.uploaded_mimetype = mimetypes.guess_type(upload.file.name)[0]
        self.uploaded_timestamp = _now()
        self.store_file(upload.file)
        path = _file_storage_path(
            self.location.url(), self.uploaded_sha1, self.uploaded_filename)
        if not default_storage.exists(path):
            default_storage.save(path, File(file))
        return Response(json_body=self.student_state())

    @XBlock.handler
    def download_assignment(self, request, suffix=''):
        path = _file_storage_path(
            self.location.url(), self.uploaded_sha1, self.uploaded_filename)
        return self.download(path,
            self.uploaded_mimetype,
            self.uploaded_filename)

    def download(self, path, mimetype, filename):
        BLOCK_SIZE = 2**10 * 8 # 8kb
        file = default_storage.open(path)
        app_iter = iter(partial(file.read, BLOCK_SIZE), '')
        return Response(
            app_iter=app_iter,
            content_type=mimetype,
            content_disposition="attachment; filename=" + filename)

    @XBlock.handler
    def staff_download(self, request, suffix=''):
        module = StudentModule.objects.get(pk=request.params['module_id'])
        state = json.loads(module.state)
        path = _file_storage_path(
            module.module_state_key, state['uploaded_sha1'],
            state['uploaded_filename'])
        return self.download(path,
            state['uploaded_mimetype'],
            state['uploaded_filename'])

    @XBlock.handler
    def get_staff_grading_data(self, request, suffix=''):
        return Response(json_body=self.staff_grading_data())

    def show_staff_grading_interface(self):
        is_course_staff = getattr(self.xmodule_runtime, 'user_is_staff', False)
        in_studio_preview = self.scope_ids.user_id is None
        return is_course_staff and not in_studio_preview


def _file_storage_path(url, sha1, filename):
    assert url.startswith("i4x://")
    path = url[6:] + '/' + sha1
    path += os.path.splitext(filename)[1]
    return path


def _get_sha1(file):
    BLOCK_SIZE = 2**10 * 8 # 8kb
    sha1 = hashlib.sha1()
    for block in iter(partial(file.read, BLOCK_SIZE), ''):
        sha1.update(block)
    file.seek(0)
    return sha1.hexdigest()


def _resource(path):
    """Handy helper for getting resources from our kit."""
    data = pkg_resources.resource_string(__name__, path)
    return data.decode("utf8")


def _now():
    return datetime.datetime.utcnow().replace(tzinfo=pytz.utc)
