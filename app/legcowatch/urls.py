from django.conf.urls import patterns, include, url

from django.contrib import admin
from django.views.generic import TemplateView
import raw.views
import common.views


admin.autodiscover()

urlpatterns = patterns('',
    # Examples:
    url(r'^$', common.views.LandingView.as_view(), name='home'),
    # url(r'^blog/', include('blog.urls')),
    
    #RAW
    #agendas
    url(r'^raw/agendas/?$', raw.views.RawCouncilAgendaListView.as_view(), name='raw_agenda_list'),
    url(r'^raw/agendas/(?P<pk>[0-9]+)/?$', raw.views.RawCouncilAgendaDetailView.as_view(), name='raw_agenda'),
    url(r'^raw/agendas/(?P<slug>[a-zA-Z0-9\-_]+)/?$', raw.views.RawCouncilAgendaDetailView.as_view(), name='raw_agenda_uid'),
    url(r'^raw/agendas/(?P<pk>[0-9]+)/source/?$', raw.views.RawCouncilAgendaSourceView.as_view(), name='raw_agenda_source'),
    url(r'^raw/agendas/(?P<slug>[a-zA-Z0-9\-_]+)/source/?$', raw.views.RawCouncilAgendaSourceView.as_view(), name='raw_agenda_source_uid'),
    
    #hansards
    url(r'^raw/hansard/?$', raw.views.RawCouncilHansardListView.as_view(), name='raw_hansard_list'),
    url(r'^raw/hansard/(?P<pk>[0-9]+)/?$', raw.views.RawCouncilHansardDetailView.as_view(), name='raw_hansard'),
    url(r'^raw/hansard/(?P<slug>[a-zA-Z0-9\-_.]+)/?$', raw.views.RawCouncilHansardDetailView.as_view(), name='raw_hansard_uid'),
    url(r'^raw/hansard/(?P<pk>[0-9]+)/source/?$', raw.views.RawCouncilHansardSourceView.as_view(), name='raw_hansard_source'),
    url(r'^raw/hansard/(?P<slug>[a-zA-Z0-9\-_.]+)/source/?$', raw.views.RawCouncilHansardSourceView.as_view(), name='raw_hansard_source_uid'),
    
    #members
    url(r'^raw/members/?$', raw.views.RawMemberListView.as_view(), name='raw_member_list'),
    url(r'^raw/members/(?P<pk>[0-9]+)/?$', raw.views.RawMemberDetailView.as_view(), name='raw_member'),
    
    #committee
    url(r'^raw/committees/?$', raw.views.RawCommitteeListView.as_view(), name='raw_committee_list'),
    url(r'^raw/committees/(?P<pk>[0-9]+)/?$', raw.views.RawCommitteeDetailView.as_view(), name='raw_committee'),
    
    #CouncilQuestion
    url(r'raw/questions/?$', raw.views.RawCouncilQuestionListView.as_view(),name='raw_question_list'),
    url(r'raw/questions/(?P<pk>[0-9]+)/?$', raw.views.RawCouncilQuestionDetailView.as_view(),name='raw_question'),
    url(r'^raw/questions/(?P<slug>[a-zA-Z0-9\-_.]+)/?$', raw.views.RawCouncilQuestionDetailView.as_view(), name='raw_question_uid'),
    url(r'^raw/questions/(?P<pk>[0-9]+)/source/?$', raw.views.RawCouncilQuestionSourceView.as_view(), name='raw_question_source'),
    url(r'^raw/questions/(?P<slug>[a-zA-Z0-9\-_.]+)/source/?$', raw.views.RawCouncilQuestionSourceView.as_view(), name='raw_question_source_uid'),
    
    #ERROR REPORT
    url(r'^error_report/?$', common.views.ErrorReportFormView.as_view(), name='error_report'),
    
    #PARSED
    url(r'^parsed/?$', raw.views.ParsedModelListView.as_view(), name='parsed_model_list'),
    url(r'^parsed/(?P<model>[a-zA-Z]+)/?$', raw.views.ParsedModelInstanceList.as_view(), name='parsed_model_instances'),
    url(r'^parsed/(?P<model>[a-zA-Z]+)/(?P<uid>[a-zA-Z0-9\-_\.]+)/?$', raw.views.ParsedModelDetailView.as_view(), name='parsed_model_detail'),
    
    #OTHERS
    url(r'^admin/', include(admin.site.urls)),
    url(r'^api-auth/', include('rest_framework.urls', namespace='rest_framework'))
)