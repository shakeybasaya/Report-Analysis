from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

urlpatterns = [
    path('', views.landing, name='landing'),

    # --- Authentication ---
    path('signup/', views.signup, name='signup'),
    path('login/', auth_views.LoginView.as_view(template_name='dashboard/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='landing'), name='logout'),

    path('import/', views.import_data, name='import_data'),

    path('clean/', views.data_cleaning, name='data_cleaning'),
    path('clean/remove-duplicates/', views.clean_remove_duplicates, name='clean_remove_duplicates'),
    path('clean/drop-missing/', views.clean_drop_missing, name='clean_drop_missing'),
    path('clean/fill-missing/', views.clean_fill_missing, name='clean_fill_missing'),

    path('visualization/', views.visualization, name='visualization'),
    path('export-pdf/', views.export_pdf, name='export_pdf'),

    path('report/', views.report_builder, name='report_builder'),
    path('report/add/', views.add_to_report, name='add_to_report'),
    path('report/remove/<int:index>/', views.remove_from_report, name='remove_from_report'),
    path('report/reorder/<int:index>/<str:direction>/', views.reorder_report, name='reorder_report'),
    path('report/edit/<int:index>/', views.edit_report_item, name='edit_report_item'
),
    path('report/export/', views.export_report_pdf, name='export_report_pdf'),

    path('report/history/', views.report_history, name='report_history'),
    path('report/history/<int:report_id>/delete/', views.delete_report, name='delete_report'),
]