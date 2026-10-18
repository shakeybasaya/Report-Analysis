from django.conf import settings
from django.db import models


class Dataset(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='datasets',
        null=True,   # null=True so `makemigrations` doesn't choke on any existing rows;
        blank=True,  # new datasets will always have a user going forward (see views.py)
    )
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to='datasets/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class DataRow(models.Model):
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name='rows'
    )

    row_number = models.IntegerField()
    data = models.JSONField()

    def __str__(self):
        return f"{self.dataset.name} - Row {self.row_number}"


class GeneratedReport(models.Model):
    """One row per 'Download PDF Report' click — stores the finished PDF
    itself (so history can re-download without regenerating) plus the
    report's metadata, so the history page can be built from this alone."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='generated_reports',
    )
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='generated_reports',
    )  # SET_NULL: importing a new dataset deletes the old one, but its past reports should still show up

    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=255, blank=True)
    prepared_by = models.CharField(max_length=255)
    organization = models.CharField(max_length=255, blank=True)
    chart_count = models.PositiveIntegerField(default=0)

    file = models.FileField(upload_to='reports/')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.user} — {self.created_at:%Y-%m-%d})"