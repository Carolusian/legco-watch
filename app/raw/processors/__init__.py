"""
Helpers to load the Scrapy JSON output into RawModels
"""
from collections import OrderedDict
from django.core.exceptions import ImproperlyConfigured
import os
from raw.processors.library_agenda import LibraryAgendaProcessor
from raw.processors.library_member import LibraryMemberProcessor
from raw.processors.question import QuestionProcessor
from raw.processors.schedule import ScheduleMemberProcessor, ScheduleCommitteeProcessor, ScheduleMembershipProcessor, \
    ScheduleMeetingCommitteeProcessor, ScheduleMeetingProcessor
from raw.processors.library_hansard import LibraryHansardProcessor
from django.conf import settings


def get_processor_for_spider(spider):
    """
    Returns the function that processes the results of a spider crawl

    Not sure what the best way to store the mapping is.
    """
    proc = PROCESS_MAP.get(spider, None)
    if proc is None:
        raise RuntimeError("Invalid spider {}".format(spider))

    return proc


# Use an OrderedDict because some processors require data from other processors
# It won't cause an error to run out of order, but it'll be missing data
PROCESS_MAP = OrderedDict([
    ('library_agenda', LibraryAgendaProcessor),
    ('library_member', LibraryMemberProcessor),
    # The below processors should be run in this order
    ('schedule_member', ScheduleMemberProcessor),
    ('schedule_committee', ScheduleCommitteeProcessor),
    ('schedule_membership', ScheduleMembershipProcessor),#a lot of 'Could not find committee committee-dddd' warnings
    ('schedule_meeting_committee', ScheduleMeetingCommitteeProcessor),
    ('schedule_meeting', ScheduleMeetingProcessor),
    ('council_question', QuestionProcessor),
    #('council_question_old', QuestionProcessor),#not implemented
    #'council hansard' is depreciated
    ('library_hansard', LibraryHansardProcessor), 
])


"""
Some scripts for testing

from raw import processors, models
a = models.ScrapeJob.objects.latest_complete_job('library_agenda')
fp = processors.file_wrapper(processors.get_items_file(a.spider, a.job_id))
items = [xx for xx in fp if xx['type'] == 'LibraryAgenda' and 'Ombudsman' not in xx['title_en']]
multi = [xx for xx in items if len(xx['links']) != 2]
foo = processors.LibraryAgendaProcessor('foo')

[foo._get_paper_number(xx) for xx in items]
[foo._filter_links(xx['links']) for xx in multi]


from raw import processors, models
job = models.ScrapeJob.objects.latest_complete_job('library_agenda')
items_file = processors.get_items_file(job.spider, job.job_id)
proc = processors.LibraryAgendaProcessor(items_file, job)
proc.process()

from raw import processors, models
items_file = processors.file_wrapper('members.jl')
items = [xx for xx in items_file]
proc = processors.LibraryMemberProcessor('members.jl')
uids = [proc._generate_uid(xx) for xx in items]
"""