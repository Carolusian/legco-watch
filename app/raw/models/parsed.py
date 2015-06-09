from collections import OrderedDict
from copy import copy
from datetime import datetime, date, time
import json
import logging
from django.conf import settings
from django.db import models, IntegrityError
from django.db.backends import BaseDatabaseWrapper
from django.db.backends.util import CursorWrapper
from django.db.models import get_model, Q
from django.utils.encoding import force_unicode
from django.utils.text import slugify
import re
from constants import GENDER_CHOICES, LANG_EN
from .raw import RawMember, RawCommittee, RawCommitteeMembership, RawCouncilAgenda, RawCouncilQuestion
from ..names import MemberName, NameMatcher
from ..docs.agenda import logger as agenda_logger
from ..docs.question import logger as question_logger
from ..docs.question import CouncilQuestion

logger = logging.getLogger('legcowatch')


class TimestampMixin(object):
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)


class BaseParsedManager(models.Manager):
    def create_from_raw(self, raw_obj):
        # Create a parsed model from its corresponding raw one, but not saving
        if getattr(self, 'excluded', None) is not None:
            excluded = copy(self.excluded)
        else:
            excluded = []
        # copy fields directly without any processing
        try:
            obj = self.get(uid=raw_obj.uid)
        except self.model.DoesNotExist:
            obj = self.model()
        for field in [xx.name for xx in obj._meta.fields]:
            if field not in excluded:
                setattr(obj, field, getattr(raw_obj, field, None))
        obj.deactivate = False
        return obj

    def _deactivate_db_debug(self):
        if settings.DEBUG:
            self.original = BaseDatabaseWrapper.make_debug_cursor
            BaseDatabaseWrapper.make_debug_cursor = lambda self, cursor: CursorWrapper(cursor, self)

    def _reactivate_db_debug(self):
        if settings.DEBUG and getattr(self, 'original') is not None:
            BaseDatabaseWrapper.make_debug_cursor = self.original

    def populate(self):
        self._deactivate_db_debug()
        raw_items = self.model.RAW_MODEL.objects.all()
        count = 0
        for item in raw_items:
            # Get or create, but without commit
            obj = self.create_from_raw(item)
            try:
                obj.save()
                count += 1
            except IntegrityError as e:
                logger.warning(u'Could not create from {}'.format(item))
                logger.warning(e)
        logger.info('Populated {} objects'.format(count))
        self._reactivate_db_debug()
        

class BaseParsedModel(models.Model):
    # We don't constrain UIDs to be unique, because we may have duplicates in the raw data that we want
    # to deactivate here
    uid = models.CharField(max_length=255)
    deactivate = models.BooleanField(default=False)

    # link to the raw model object we're mapping to
    RAW_MODEL = None
    objects = BaseParsedManager()

    class Meta:
        abstract = True
        app_label = 'raw'   
    
    def get_overridable_fields(self):
        # First we get the fields for the model
        # if the model has `not_overridable`, then we exclude it from the list
        exclude = getattr(self, 'not_overridable', [])
        # By default, we want to exclude things like the uid
        exclude.extend(['created', 'modified', 'uid', 'id'])
        # Now iterate over the model fields to get the field names we want
        fields = []
        for field in self._meta.concrete_fields:
            if field.name not in exclude:
                fields.append(field)
        return fields


class OverrideManager(models.Manager):
    def get_from_reference(self, reference):
        # Tries to retrieve the override for a specific model instance
        model = reference._meta.model_name
        ref_uid = reference.uid
        try:
            return self.get(ref_model=model, ref_uid=ref_uid)
        except self.model.DoesNotExist:
            return None

    def get_or_create_from_reference(self, reference):
        res = self.get_from_reference(reference)
        if res is None:
            res = self.create_from(reference)
        return res

    def get_for_class(self, class_name):
        # Retrieves all of the overrides for a specific model
        return self.filter(ref_model=class_name.lower())

    def create_from(self, reference):
        # Creates an uncommitted override for the reference
        model = reference._meta.model_name
        ref_uid = reference.uid
        instance = self.model(ref_model=model, ref_uid=ref_uid)
        return instance


class Override(models.Model):
    # The lowercase string name of the model we're referencing, model._meta.model_name
    ref_model = models.CharField(max_length=100, null=False)
    # The uid of the object we are overriding
    # Don't refer to id, because we want to be able to still look up
    # The reference if it is recreated and gets a new auto id
    ref_uid = models.CharField(max_length=100, unique=True)
    # Where the serialized override data is stored.  'deactivate' is a special key in this
    data = models.TextField(blank=True, null=False, default='')
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    objects = OverrideManager()

    class Meta:
        app_label = 'raw'

    def __unicode__(self):
        return u'{} {}'.format(self.ref_model, self.ref_uid)

    def _get_model(self):
        # Gets the model class from the string name
        # We assume all of the models are in the Raw app
        res = get_model('raw', self.ref_model)
        return res

    def get_reference(self):
        # Gets the reference object
        model = self._get_model()
        try:
            instance = model.objects.get(id=self.ref_uid)
        except model.DoesNotExist:
            logger.warn('Instance of {} with id {} does not exist'.format(self.ref_model, self.ref_uid))
            return None
        return instance

    def get_payload(self):
        # Returns the unserialized data payload
        if self.data == u'':
            return {}
        return json.loads(self.data)

    def merge_payload(self, data_to_merge):
        # Takes a dict and merges it the current payload
        current = self.get_payload()
        current.update(data_to_merge)
        self.data = json.dumps(current)

    def is_deactivated(self):
        payload = self.get_payload()
        return payload.get('deactivate', False)

"""
Person
"""
class PersonManager(models.Manager):
    def create_from_raw(self, raw_obj):
        try:
            obj = self.get(uid=raw_obj.uid)
        except self.model.DoesNotExist:
            obj = self.model()
        # Copy items over, but a few fields require special handling
        excluded = ['education_e', 'education_c', 'occupation_e', 'occupation_c']
        for field in [xx.name for xx in obj._meta.fields]:
            if field not in excluded:
                setattr(obj, field, getattr(raw_obj, field, None))
        # Concatenate the education and occupation fields
        for field in excluded:
            raw_val = getattr(raw_obj, field)
            if raw_val is None or raw_val == u'':
                continue
            json_val = json.loads(raw_val)
            setattr(obj, field, ', '.join(json_val))
        obj.deactivate = False
        return obj


class ParsedPerson(TimestampMixin, BaseParsedModel):
    name_e = models.CharField(max_length=100)
    name_c = models.CharField(max_length=100)
    title_e = models.CharField(max_length=100)
    title_c = models.CharField(max_length=100)
    honours_e = models.CharField(max_length=50, blank=True, default='')
    honours_c = models.CharField(max_length=50, blank=True, default='')
    education_e = models.TextField(blank=True, default='')
    education_c = models.TextField(blank=True, default='')
    occupation_e = models.TextField(blank=True, default='')
    occupation_c = models.TextField(blank=True, default='')
    gender = models.IntegerField(choices=GENDER_CHOICES)
    year_of_birth = models.IntegerField(null=True, blank=True)
    place_of_birth = models.CharField(max_length=50, blank=True, default='')
    homepage = models.TextField(blank=True, default='')
    photo_file = models.TextField(blank=True, default='')
    
    #Relationships
    committees = models.ManyToManyField('ParsedCommittee', through='ParsedCommitteeMembership')
    #what to do with service membership?
    objects = PersonManager()

    not_overridable = ['photo_file']

    RAW_MODEL = RawMember

    def __unicode__(self):
        return u"{} {}".format(unicode(self.name_e), unicode(self.name_c))

    def get_name_object(self, english=True):
        if english:
            return MemberName(self.name_e)
        else:
            return MemberName(self.name_c)

    @classmethod
    def get_matcher(cls, english=True):
        all_members = cls.objects.all()
        names = [(xx.get_name_object(english), xx) for xx in all_members]
        matcher = NameMatcher(names)
        return matcher


class MembershipManager(models.Manager):
    def get_active_on_date(self, query_date):
        # Return True if a membership is active on a given query_date.
        return self.filter(start_date__lt=query_date, end_date__gt=query_date)

    def get_current(self):
        # Finds memberships where end date is None or later than today
        today = date.today()
        return self.filter(Q(start_date__lt=today), Q(end_date__gt=today) | Q(end_date=None))

    def create_from_raw(self, person):
        # Create all the memberships from a RawMember.  So could result in multiple new objects
        service_objects = json.loads(person.service_e)
        for service in service_objects:
            parsed = MembershipParser(service)
            uid = ParsedMembership.make_uid(person, parsed)
            # Get or create
            try:
                obj = self.get(uid=uid)
            except self.model.DoesNotExist:
                obj = self.model()

            # Copy data over
            fields_to_copy = ['start_date', 'end_date', 'method_obtained', 'position', 'note']
            for field in fields_to_copy:
                # don't fill in nulls so defaults work
                val = getattr(parsed, field, None)
                if val is not None:
                    setattr(obj, field, val)
            obj.deactivate = False
            # Need a ParsedPerson
            parsed_person = ParsedPerson.objects.get(uid=person.uid)
            obj.person = parsed_person
            obj.uid = uid
            yield obj

    def populate(self):
        raw_items = RawMember.objects.all()
        for item in raw_items:
            for membership in self.create_from_raw(item):
                membership.save()


class ParsedMembership(TimestampMixin, BaseParsedModel):
    """
    Model for each position and term that a person holds.

    The UID for this should be something like '{member_uid}.m{start_date}.{position_slug}', however it is conceivable
    that a person may begin to hold multiple offices at the same time.  Currently, this is the case
    for only one record, which appears to be erroneous, but that doesn't mean it can't happen in the future
    """
    person = models.ForeignKey(ParsedPerson, related_name='memberships')
    start_date = models.DateField(null=False)
    end_date = models.DateField(null=True)
    method_obtained = models.CharField(max_length=255)
    position = models.CharField(max_length=255)
    note = models.CharField(max_length=255, default='')

    objects = MembershipManager()

    not_overridable = ['person']
    RAW_MODEL = RawMember

    class Meta:
        ordering = ['-start_date']
        app_label = 'raw'

    def __unicode__(self):
        return '{}: {} - {}'.format(self.position, self.start_date, self.end_date)

    @staticmethod
    def make_uid(person_obj, membership_parser):
        member_uid = person_obj.uid
        # Can't use strftime because some years < 1900
        # YYYYMMDD
        start_date = membership_parser.start_date.isoformat().replace('-', '')
        slug = slugify(membership_parser.position)
        return '{}.{}.{}'.format(member_uid, start_date, slug)


class MembershipParser(object):
    # Container for parsing membership data in RawMember.service_e and service_c
    # See create_from_raw() in MembershipManager for usage
    # We only care about the English, and we'll use that as the basis for translation into Chinese
    DATE_RE = r'(?P<start>\d+ \w+ \d+) - (?P<end>\d+ \w+ \d+)?'
    DETAIL_RE = r'^(?P<method>\w+) \((?P<position>.+)\)$$'
    DATE_FORMAT = '%d %B %Y'

    def __init__(self, membership_obj, lang='E'):
        self._raw = membership_obj
        self.start_date, self.end_date = self.parse_dates(membership_obj[0])
        method, position = self.parse_position_method(membership_obj[1])
        # Appointed or Elected
        self.method_obtained = method
        # Ex Officio - Chief Secretary, Geographical Constituency - Kowloon East
        self.position = position
        # Retired, replaced, resigned, etc.
        if len(membership_obj) > 2:
            self.note = self.parse_note(membership_obj[2])
        else:
            self.note = None

    def parse_date(self, date_str):
        if date_str is None:
            return None
        try:
            return datetime.strptime(date_str, self.DATE_FORMAT).date()
        except ValueError:
            return None

    def parse_dates(self, date_string):
        # First element of the json object is the string
        matched_dates = re.match(self.DATE_RE, date_string)
        if matched_dates is None:
            return None, None

        matches = matched_dates.groups()
        return self.parse_date(matches[0]), self.parse_date(matches[1])

    def parse_position_method(self, position_string):
        matched = re.match(self.DETAIL_RE, position_string)
        if matched is None:
            return None, None

        groups = matched.groups()
        return groups[0], groups[1]

    def parse_note(self, note_string):
        res = note_string.replace('(', '').replace(')', '')
        return res


class ParsedCommittee(TimestampMixin, BaseParsedModel):
    code = models.CharField(max_length=100, blank=True)
    name_e = models.TextField()
    name_c = models.TextField()
    url_e = models.TextField(blank=True, default='')
    url_c = models.TextField(blank=True, default='')
    members = models.ManyToManyField(ParsedPerson, through='ParsedCommitteeMembership')

    RAW_MODEL = RawCommittee

    class Meta:
        ordering = ['name_e']
        app_label = 'raw'

    def __unicode__(self):
        return u'{}: {} {}'.format(self.code, self.name_e, self.name_c)


class CommitteeMembershipManager(BaseParsedManager):
    excluded = ['person', 'committee']

    def get_active_on_date(self, query_date):
        return self.filter(start_date__lt=query_date, end_date__gt=query_date)

    def get_current(self):
        # Finds memberships where end date is None
        today = date.today()
        return self.filter(Q(start_date__lt=today), Q(end_date__gt=today) | Q(end_date=None))

    def create_from_raw(self, raw_obj):
        obj = super(CommitteeMembershipManager, self).create_from_raw(raw_obj)
        # String up the person and the committee
        raw_committee = raw_obj.committee
        if raw_committee is not None:
            committee = ParsedCommittee.objects.get(uid=raw_committee.uid)
            obj.committee = committee

        if raw_obj.member is not None:
            raw_member = raw_obj.member.get_raw_member()
            if raw_member is not None:
                person = ParsedPerson.objects.get(uid=raw_member.uid)
                obj.person = person
        return obj


class ParsedCommitteeMembership(TimestampMixin, BaseParsedModel):
    """
    Many to many table linking ParsedPerson and ParsedCommittee.
    """
    committee = models.ForeignKey(ParsedCommittee, related_name='memberships')
    person = models.ForeignKey(ParsedPerson, related_name='committee_memberships')
    post_e = models.CharField(max_length=100, blank=True, default='')
    post_c = models.CharField(max_length=100, blank=True, default='')
    start_date = models.DateTimeField(null=False)
    end_date = models.DateTimeField(null=True)

    objects = CommitteeMembershipManager()
    RAW_MODEL = RawCommitteeMembership

    class Meta:
        app_label = 'raw'

    def __unicode__(self):
        return u'{} - {} - {}'.format(self.post_e, self.start_date, self.end_date)


class CouncilMeetingManager(BaseParsedManager):
    excluded = ['start_date', 'end_date']

    def create_from_raw(self, raw_obj):
        # Need to snip off the language on the agenda UID
        new_uid = u'cmeeting-{:%Y%m%d}'.format(raw_obj.start_date)
        try:
            obj = self.get(uid=new_uid)
        except self.model.DoesNotExist:
            obj = self.model()

        obj.uid = new_uid
        start_date = raw_obj.start_date
        obj.start_date = datetime.combine(start_date, time(11, 0))
        obj.deactivate = False
        return obj

    def get_from_raw(self, raw_obj):
        # Get a ParsedCouncilMeeting from a RawCouncilAgenda
        new_uid = u'cmeeting-{:%Y%m%d}'.format(raw_obj.start_date)
        try:
            return self.get(uid=new_uid)
        except self.model.DoesNotExist:
            return None


class ParsedCouncilMeeting(TimestampMixin, BaseParsedModel):
    """
    Meetings can span multiple days, but have only one agenda for all of the business that will occur on that day
    """
    # Usual time of start is 11am
    start_date = models.DateTimeField(null=False)
    end_date = models.DateTimeField(null=True)

    objects = CouncilMeetingManager()
    RAW_MODEL = RawCouncilAgenda

    class Meta:
        app_label = 'raw'

    def __unicode__(self):
        if self.end_date is None:
            return u'Meeting on {}'.format(self.start_date.date())
        else:
            return u'Meeting on {} to {}'.format(self.start_date.date(), self.end_date.date())


class QuestionManager(BaseParsedManager):
    
    def create_from_raw(self, raw_obj):
        # We assume raw_obj is in English
        
        #obj = ParsedQuestion()
        # we can get a lot of info from raw uid
        raw_uid = raw_obj.uid
        date_str = raw_uid.split('-')[1]
        
        # locate the council meeting/agenda in which this question appears
        meeting_uid = u'cmeeting-{}'.format(date_str)
        try:
            meeting = ParsedCouncilMeeting.objects.get(uid=meeting_uid)
        except ParsedCouncilMeeting.DoesNotExist:
            # Sometimes a meeting is cancelled or delayed - in this case we can ignore this question
            # e.g. 2013.05.15
            logger.warn(u'Cannot find a meeting for question:{} - required meeting uid: {}'.format(raw_uid,date_str))
            return None #because we cannot generate a uid
        if meeting is not None:
            # Make a uid
            new_uid = ParsedQuestion.generate_uid(meeting, raw_obj.number, raw_obj.is_urgent)
            # Get or create
            try:
                obj = ParsedQuestion.objects.get(uid=new_uid)
            except ParsedQuestion.DoesNotExist:
                obj = ParsedQuestion()
                obj.uid = new_uid
        
        obj.meeting = meeting
            
        # Match the number of question
        obj.number = raw_obj.number
        # Urgent?
        obj.urgent = raw_obj.is_urgent
        # Oral or written
        obj.question_type = ParsedQuestion.ORAL if raw_obj.is_oral else ParsedQuestion.WRITTEN
        # Asker
        if raw_obj.asker is not None: 
            #raw_obj.asker is already a foreign key, and member uid does not change from raw to parsed
            person = ParsedPerson.objects.get(uid=raw_obj.asker.uid)
        else:
            # Sometimes the NameMatcher does not work, so no asker FK was stored in model
            # we can try our luck in other language
            q_otherlang = raw_obj.get_lang_counterpart()
            if q_otherlang.asker is not None:
                person = ParsedPerson.objects.get(uid=q_otherlang.asker.uid)    
        if 'person' in locals():
            if person is not None:
                obj.asker = person
        else:
            logger.warn('Cannot find asker for question {} with name "{}"'.format(raw_obj.uid,raw_obj.asker))
        
        # Need to use question parser from here on
        # Need both languages
        uid_cn = raw_obj.uid[:-1] + u'c'
        q_parser_en = raw_obj.get_parser()
        q_parser_cn = RawCouncilQuestion.objects.get_by_uid(uid_cn).get_parser()
        # sometimes (rarely) parser returns a NoneType
        if q_parser_en and q_parser_cn:
            # Replier(s)
            obj.repliers_e = q_parser_en.repliers
            obj.repliers_c = q_parser_cn.repliers
            # Question subject
            obj.ask_subject_e = q_parser_en.subject
            obj.ask_subject_c = q_parser_cn.subject
            # Reply subject
            obj.reply_subject_e = q_parser_en.question_title
            obj.reply_subject_c = q_parser_cn.question_title
            # Question body
            obj.body_e = q_parser_en.question_content
            obj.body_c = q_parser_cn.question_content
            # Reply body
            obj.reply_e = q_parser_en.reply_content
            obj.reply_c = q_parser_cn.reply_content
        
        return obj
        
    def populate(self, dry_run=False):
        self._deactivate_db_debug()
        question_logger.deactivate = False
        # use English version as base, fill in Chinese info later
        en_questions = RawCouncilQuestion.objects.filter(uid__endswith=u'e').order_by('raw_date')
        count = 0
        for raw_question in en_questions:
            # Get or create, but without commit
            obj = self.create_from_raw(raw_question)
            if obj is not None:
                try:
                    obj.save()
                    count += 1
                except IntegrityError as e:
                    logger.warning(u'Could not create from {}'.format(raw_question))
                    logger.warning(e)
        logger.info('Populated {} questions'.format(count))
        question_logger.deactivate = True
        self._reactivate_db_debug()
        
        
        ################ Depreciated - we parsed the Q&A from web page ##################
        """
        Create ParsedQuestions.  Strategy is to start with all RawCouncilQuestions, get the basic information from there,
        then cross reference against the CouncilAgenda for that date.

        Since CouncilAgenda parsing is not perfect, will probably result in a lot of missing cross references.  In these
        cases, we just don't populate the question's body.
        """
#         self._deactivate_db_debug()
#         # Deactivate the agenda logging
#         agenda_logger.deactivate = True
#         parser_cache = OrderedDict()
#         all_questions = RawCouncilQuestion.objects.order_by('raw_date').all()
#         name_matcher_e = ParsedPerson.get_matcher()
#         name_matcher_c = ParsedPerson.get_matcher(False)
#         count = 0
#         skipped = 0
#         incomplete = 0
#         for raw_question in all_questions:
#             # Get the agenda
#             agenda = raw_question.get_agenda()
#             if agenda is None:
#                 logger.warn(u'Could not find agenda ({}).'.format(agenda))
#                 skipped += 1
#                 continue
#  
#             meeting = ParsedCouncilMeeting.objects.get_from_raw(agenda)
#             if meeting is None:
#                 logger.warn(u'Could not find meeting ({}).'.format(meeting, agenda))
#                 skipped += 1
#                 continue
# 
#             # Get the parser and cache it
#             if agenda.uid in parser_cache:
#                 parser = parser_cache[agenda.uid]
#             else:
#                 parser = agenda.get_parser()
#                 parser_cache[agenda.uid] = parser
# 
#             # Now try to find the question in the parser
#             # CouncilAgenda's can't yet tell if a question is urgent or not, so we check against the asker
#             if parser is not None:
#                 agenda_question = raw_question.get_matching_question_from_parser(parser)
#             else:
#                 agenda_question = None
# 
#             # if we couldn't find it, then log it
#             if agenda_question is None:
#                 logger.warn(u'Could not find corresponding Agenda question for {}'.format(raw_question))
#                 incomplete += 1
# 
#             # Check that the names on the askers match, then get the ParsedPerson object that we'll attach to the question
#             is_english = raw_question.language == LANG_EN
#             agenda_name = None
#             raw_name = None
#             if raw_question.asker is not None:
#                 raw_name = raw_question.asker.get_name_object(is_english)
#             if agenda_question is not None and agenda_question.asker is not None:
#                 agenda_name = MemberName(agenda_question.asker)
# 
#             asker = None
#             if raw_name is not None:
#                 if agenda_name is not None:
#                     if agenda_name != raw_name:
#                         logger.warn(u"Askers don't match on question {}.  Agenda: {} Raw: {}".format(
#                             force_unicode(raw_question), force_unicode(agenda_name), force_unicode(raw_name)))
#                 # Either way, we use the raw_question's asker
#                 asker = ParsedPerson.objects.get(uid=raw_question.asker.uid)
#             else:
#                 if agenda_name is not None:
#                     # Try to find a name match based on the agenda name
#                     matcher = name_matcher_e if is_english else name_matcher_c
#                     match = matcher.match(agenda_name)
#                     if match is not None:
#                         asker = match[1]
#                 else:
#                     asker = None
# 
#             # Can't find an asker, abort the creation
#             if asker is None:
#                 logger.warn(u'Could not find an asker for the question {}'.format(raw_question))
#                 skipped += 1
#                 continue
# 
#             # Check some other fields to see if they match, for diagnostic purposes
#             if agenda_question is not None:
#                 raw_question.validate_question(agenda_question)
# 
#             # Create the ParsedQuestion object and populate the fields
#             q_uid = ParsedQuestion.generate_uid(meeting, raw_question.number, raw_question.is_urgent)
#             try:
#                 obj = self.get(uid=q_uid)
#             except self.model.DoesNotExist:
#                 obj = self.model(uid=q_uid)
# 
#             obj.meeting = meeting
#             obj.number = raw_question.number
#             obj.urgent = raw_question.is_urgent
#             obj.question_type = ParsedQuestion.ORAL if raw_question.is_oral else ParsedQuestion.WRITTEN
#             obj.asker = asker
#             if is_english:
#                 if agenda_question is not None:
#                     # Only keep the English replier
#                     obj.replier = agenda_question.replier or u''
#                     obj.body_e = agenda_question.body
#                 obj.subject_e = raw_question.subject
#             else:
#                 if agenda_question is not None:
#                     obj.body_c = agenda_question.body
#                 obj.subject_c = raw_question.subject
#             count += 1
#             if not dry_run:
#                 obj.save()
# 
#             # Prune the parser cache so we don't keep all of the CouncilAgendas in memory
#             if len(parser_cache) > 10:
#                 parser_cache.popitem(last=False)
# 
#         logger.info(u'{} questions created, {} skipped, {} without body text'.format(count, skipped, incomplete))
#         agenda_logger.deactivate = False
#         self._reactivate_db_debug()
        

class ParsedQuestion(TimestampMixin, BaseParsedModel):
    """
    Questions asked during LegCo meetings
    """
    ORAL = 1
    WRITTEN = 2
    QTYPES = (
        (ORAL, 'Oral'),
        (WRITTEN, 'Written')
    )
    meeting = models.ForeignKey(ParsedCouncilMeeting, related_name='questions')
    number = models.IntegerField()
    urgent = models.BooleanField(default=False)
    question_type = models.IntegerField(choices=QTYPES,blank=True,default=None)
    asker = models.ForeignKey(ParsedPerson, related_name='questions', blank=True, default=None)
    # Secretary (or Secretaries) of the government
    repliers_e = models.TextField(default='',blank=True)
    repliers_c = models.TextField(default='',blank=True)
    # Actual content of the question
    ask_subject_e = models.TextField(default='',blank=True)
    ask_subject_c = models.TextField(default='',blank=True)
    body_e = models.TextField(default='',blank=True)
    body_c = models.TextField(default='',blank=True)
    reply_subject_e = models.TextField(default='',blank=True)
    reply_subject_c = models.TextField(default='',blank=True)
    reply_e = models.TextField(default='',blank=True)
    reply_c = models.TextField(default='',blank=True)

    objects = QuestionManager()
    RAW_MODEL = RawCouncilQuestion
    
    class Meta:
        app_label = 'raw'
        ordering = ['meeting']

    def __unicode__(self):
        return u'{} - Q{} - {}'.format(self.meeting, self.number, self.ask_subject_e)

    @classmethod
    def generate_uid(cls, meeting, number, is_urgent):
        return u'{}-{}q{}'.format(meeting.uid, u'u' if is_urgent else u'', number)
