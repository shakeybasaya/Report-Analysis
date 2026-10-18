# ---------------------------------------------------------------------
# IMPORTS: tools (libraries) that this file needs
# ---------------------------------------------------------------------

# io lets us work with "in-memory files" (data kept in RAM instead of saved on disk)
import io
# json lets us read and write JSON data (a text format for storing data)
import json
# re is for "regular expressions", a way to search for patterns in text
import re
# textwrap breaks long text into shorter lines so it fits on a PDF page
import textwrap
# base64 turns binary data (like a picture) into plain text so it can sit inside HTML
import base64
# warnings lets us hide or control Python warning messages
import warnings
# urlencode turns a dictionary into a URL query string, e.g. {'a': 1} -> "a=1"
from urllib.parse import urlencode

# numpy is a math library; we use it for numbers and missing values (NaN)
import numpy as np
# pandas is the main data-table library; a DataFrame is like an Excel sheet in Python
import pandas as pd
# matplotlib is the library that draws the charts
import matplotlib

# 'Agg' tells matplotlib to draw into images only, with no pop-up window (needed on a web server)
matplotlib.use('Agg')
# pyplot is matplotlib's simple drawing interface; we call it "plt" for short
import matplotlib.pyplot as plt
# Rectangle lets us draw rectangles (used for the lines and rules on the PDF pages)
from matplotlib.patches import Rectangle
# PdfPages lets us save several drawn pages into one PDF file
from matplotlib.backends.backend_pdf import PdfPages

# render shows an HTML template, redirect sends the user to another page,
# get_object_or_404 fetches a database row or shows a "404 Not Found" page
from django.shortcuts import render, redirect, get_object_or_404
# HttpResponse lets us send back plain text or a file (like a PDF) instead of a page
from django.http import HttpResponse
# transaction makes several database changes happen "all together or not at all"
from django.db import transaction
# ContentFile wraps raw bytes so Django can save them as a file
from django.core.files.base import ContentFile
# messages lets us show little notices to the user (success, warning, error)
from django.contrib import messages
# auth_login logs a user in; we rename it to avoid confusion with other "login" names
from django.contrib.auth import login as auth_login
# login_required blocks a page unless the user is logged in
from django.contrib.auth.decorators import login_required
# UserCreationForm is Django's ready-made sign-up form
from django.contrib.auth.forms import UserCreationForm
# reverse turns a page name (like 'visualization') into its real web address, so we can send the user there
from django.urls import reverse

# import the database tables (models) this app uses
from .models import Dataset, DataRow, GeneratedReport

# ---------------------------------------------------------------------
# CONSTANTS: fixed settings used in many places
# ---------------------------------------------------------------------

# the most dots we will draw on a scatter chart (more than this would be slow and messy)
MAX_POINTS = 3000
# the most categories we will show on a bar/line/pie chart
MAX_CATEGORIES = 30
# the most rows shown in the "View Data" preview table
DATA_PREVIEW_MAX_ROWS = 200

# the name (key) under which the report queue is stored in the user's session
REPORT_SESSION_KEY = 'report_queue'

# The calculations the user can pick for each group on the Analysis page (the "group by" calculation).
# The key is the pandas function name, and the value is the friendly word shown to the user.
AGG_LABELS = {
    'sum': 'Sum',
    'count': 'Count',
    'mean': 'Average',
    'max': 'Maximum',
    'min': 'Minimum',
    'std': 'Standard Deviation',
    'nunique': 'Distinct Count',
    'total': 'Total Records',
}
AGG_WORDS = {key: label.lower() for key, label in AGG_LABELS.items()}

# Only accept calculations implemented by the chart builder.
def _clean_agg(value):
    return value if value in AGG_LABELS else 'sum'

# Date grouping options for line charts.
DATE_INTERVALS = {'original', 'day', 'month', 'quarter', 'year'}
def _clean_date_interval(value):
    return value if value in DATE_INTERVALS else 'original'


# Helper: turns the text from a text box (one point per line) into a clean list of lines.
def _text_to_lines(text):
    # split into lines, trim the spaces, and drop the empty ones
    return [line.strip() for line in (text or '').splitlines() if line.strip()]


# Helper: True if a chart saved in the report uses exactly the same settings as the chart on the screen.
def _same_chart_settings(saved_config, ctx):
    # every one of these settings must match
    return (
            saved_config.get('chart_type') == ctx['chart_type']
            and saved_config.get('x_column') == ctx['x_column']
            and saved_config.get('y_column') == ctx['y_column']
            and saved_config.get('color_column') == ctx['color_column']
            # older queue items were saved before this option existed, so a missing value means 'sum'
            and _clean_agg(saved_config.get('agg')) == ctx['agg']
            and _clean_date_interval(saved_config.get('date_interval')) == ctx['date_interval']
            # compare the matrix columns in a fixed order so the order of ticking does not matter
            and sorted(saved_config.get('matrix_columns') or []) == sorted(ctx['matrix_columns'] or [])
    )


# CONCEPT_GUIDES is a dictionary of explanations, one per chart type.
# (Your template now has its own guide text, so this one is no longer shown.)
CONCEPT_GUIDES = {
    # guide for the bar chart
    'bar': {
        # the heading shown at the top of the guide
        'title': 'Bar Chart (Categorical Comparison)',
        # a one-line explanation of what the chart is
        'description': 'Displays metric values for discrete categories using proportional rectangular bars.',
        # what situations suit this chart
        'best_used_for': 'Comparing company revenues, costs by department, or margins across different business sectors.',
        # how to read the chart
        'how_to_interpret': 'Look for top and bottom performers. The height/length of each bar directly reflects the aggregated metric (e.g., Average Revenue or Total Expense).',
        # common mistakes to avoid
        'watchouts': 'Always check if bars start at zero to avoid exaggerating small differences! Never confuse Count with monetary magnitude.',
        # a real-life comparison to make it easy to remember
        'analogy': 'Like ranking athletes on a leaderboard—instantly spots who is winning and who is lagging.',
        # the group this chart belongs to
        'category': 'Comparison',
    },
    # guide for the line chart (same fields as above)
    'line': {
        'title': 'Line Chart (Trend Analysis)',
        'description': 'Plots a metric across ordered categories or time to reveal trends and momentum.',
        'best_used_for': 'Tracking revenue growth, expense trends, or seasonal patterns over successive periods.',
        'how_to_interpret': 'Focus on the slope between points — steep upward moves signal acceleration, flat or declining segments signal stagnation or risk.',
        'watchouts': "Don't connect unrelated or unordered categories with a line — it implies a trend that may not exist. Watch for missing periods that create misleading gaps.",
        'analogy': 'Like a heart-rate monitor — the shape of the line tells the story, not any single point.',
        'category': 'Trend',
    },
    # guide for the pie chart
    'pie': {
        'title': 'Pie Chart (Composition / Share)',
        'description': "Shows how a whole is divided into parts, sized by each category's share of the total.",
        'best_used_for': 'Showing market share, expense breakdown by category, or portfolio composition.',
        'how_to_interpret': 'Compare slice sizes, not exact values. The largest slice is the dominant contributor to the total.',
        'watchouts': 'Avoid pie charts with more than 6–8 slices — small slivers become unreadable. Never use pie charts to compare two separate totals.',
        'analogy': "Like slicing a pizza — you can see who gets the biggest piece, but not by how much more they're actually eating.",
        'category': 'Composition',
    },
    # guide for the scatter plot
    'scatter': {
        'title': 'Scatter Plot (Relationship Analysis)',
        'description': 'Plots two numeric variables against each other to reveal correlation or clustering.',
        'best_used_for': 'Testing whether one metric (e.g., marketing spend) moves with another (e.g., revenue).',
        'how_to_interpret': 'An upward-sloping cloud of points suggests a positive relationship; a downward slope suggests a negative one. A random scatter suggests little relationship.',
        'watchouts': 'Correlation is not causation. Outliers can distort the visual pattern — check individual points before drawing conclusions.',
        'analogy': "Like plotting height against shoe size across a classroom — a pattern hints at a relationship, but doesn't prove one causes the other.",
        'category': 'Relationship',
    },
    # guide for the colored scatter plot
    'colored_scatter': {
        'title': 'Colored Scatter Plot (Segmented Relationship Analysis)',
        'description': 'A scatter plot with points grouped and colored by a categorical variable, layering a third dimension onto the relationship.',
        'best_used_for': 'Comparing how a relationship (e.g., cost vs. output) differs across branches, products, or customer segments.',
        'how_to_interpret': 'Look at whether each colored group follows its own pattern, or whether all groups overlap into one general trend.',
        'watchouts': 'Too many categories create a cluttered, unreadable legend. Overlapping colors can hide subgroup patterns.',
        'analogy': 'Like a group photo where each team wears a different jersey color — you can spot which team clusters together.',
        'category': 'Relationship',
    },
    # guide for the distribution (histogram)
    'distribution': {
        'title': 'Distribution (Histogram)',
        'description': 'Shows how frequently values of a single numeric metric occur across a range.',
        'best_used_for': "Understanding the spread of loan amounts, salaries, transaction sizes, or any single metric's variability.",
        'how_to_interpret': 'Look at the shape: a tall central peak means most values cluster near the average; a skewed tail means some outliers pull the average up or down.',
        'watchouts': "Bin width choice changes the shape — too few bins hide detail, too many create noise. Don't assume a normal distribution without checking the shape.",
        'analogy': "Like sorting a class's exam scores into grade buckets — you instantly see if most students passed comfortably or if scores were all over the place.",
        'category': 'Distribution',
    },
    # guide for the scatter matrix
    'scatter_matrix': {
        'title': 'Scatter Matrix (Multi-Variable Relationships)',
        'description': 'A grid of scatter plots comparing every pair of selected numeric variables at once.',
        'best_used_for': 'Quickly screening several financial metrics together to spot which pairs are worth deeper analysis.',
        'how_to_interpret': 'Scan for diagonal patterns (strong correlation) and tight clusters (possible subgroups) across each cell.',
        'watchouts': "With more than 4 variables the grid becomes cramped and hard to read. Diagonal histograms show distribution, not relationship — don't misread them as scatter cells.",
        'analogy': 'Like a round-robin tournament bracket — every pair faces off once, so you can see all matchups in a single view.',
        'category': 'Relationship',
    },
}


# ---------------------------------------------------------------------
# READING THE UPLOADED FILE
# ---------------------------------------------------------------------

# This function takes the file the user uploaded and turns it into a pandas table (DataFrame).
def read_uploaded_file(uploaded_file):
    # get the file name in lowercase so ".CSV" and ".csv" are treated the same
    name = uploaded_file.name.lower()

    # if the name ends with .csv, it is a CSV file
    if name.endswith('.csv'):
        # remember what kind of file it is
        file_kind = 'csv'
        # read the CSV straight into a table
        df = pd.read_csv(uploaded_file)
    # if it ends with .xlsx or .xls, it is an Excel file
    elif name.endswith(('.xlsx', '.xls')):
        # remember what kind of file it is
        file_kind = 'excel'
        # read the Excel sheet into a table
        df = pd.read_excel(uploaded_file)
    # if it ends with .json, it is a JSON file
    elif name.endswith('.json'):
        # remember what kind of file it is
        file_kind = 'json'
        # use our own helper (defined below) to read the JSON file
        df = _read_json_file(uploaded_file)
    # anything else is not supported
    else:
        # stop and tell the user which file types are allowed
        raise ValueError("Please upload a CSV, Excel, or JSON file.")

    # if most column names look like "Unnamed: 0" (a sign the real header is on a lower row)...
    if file_kind in ('csv', 'excel') and _mostly_unnamed(df.columns):
        # go back to the start of the file so we can read it again
        uploaded_file.seek(0)
        # read it again, this time finding the real header row first
        df = _reread_with_detected_header(uploaded_file, file_kind == 'excel')

    # give the finished table back to whoever called this function
    return df


# Helper (the leading underscore means "for internal use"): reads a JSON file into a table.
def _read_json_file(uploaded_file):
    # make sure we start reading from the beginning of the file
    uploaded_file.seek(0)
    # try the simple way first
    try:
        # pandas can read JSON that is already shaped like a table
        df = pd.read_json(uploaded_file)
    # if that fails, try a more flexible method
    except ValueError:
        # go back to the start of the file again
        uploaded_file.seek(0)
        # load the JSON into plain Python lists/dictionaries
        raw = json.load(uploaded_file)
        # if the JSON is a list of records...
        if isinstance(raw, list):
            # flatten it into a table (nested values become extra columns)
            df = pd.json_normalize(raw)
        # if the JSON is a single object (dictionary)...
        elif isinstance(raw, dict):
            # find the first value in it that is a list (that list is likely the records)
            list_value = next((v for v in raw.values() if isinstance(v, list)), None)
            # if a list was found, flatten it; otherwise treat the whole object as one record
            df = pd.json_normalize(list_value) if list_value is not None else pd.json_normalize([raw])
        # anything else (like a bare number or string) cannot be turned into a table
        else:
            # stop with a clear error message
            raise ValueError(
                "Unsupported JSON structure — expected a list of records, "
                "or an object containing one."
            )

    # if the table came out empty...
    if df.empty:
        # tell the user there was nothing usable
        raise ValueError("The JSON file did not contain any usable records.")
    # return the finished table
    return df


# Helper: decides if most column names are "Unnamed: ..." (meaning the header row was missed).
def _mostly_unnamed(columns, threshold=0.5):
    # count how many columns there are
    total = len(columns)
    # with no columns there is nothing to check
    if total == 0:
        return False
    # count the columns whose name starts with "Unnamed:"
    unnamed = sum(1 for c in columns if str(c).startswith('Unnamed:'))
    # True when at least half (threshold) of the columns are unnamed
    return (unnamed / total) >= threshold


# Helper: guesses which of the first few rows is the real header (column names).
def _detect_header_row(raw_df, max_scan=10):
    # start by assuming row 0 is the best, with a score lower than any real score
    best_row, best_score = 0, -1
    # look at the first rows only (up to max_scan, or fewer if the file is short)
    for i in range(min(max_scan, len(raw_df))):
        # take row number i
        row = raw_df.iloc[i]
        # count how many cells in this row are filled in
        non_null = row.notna().sum()
        # count how many of those cells are non-empty text (headers are usually text)
        string_count = sum(
            isinstance(v, str) and v.strip() != '' for v in row if pd.notna(v)
        )
        # the score is higher for rows that are full and contain lots of text
        score = non_null + string_count
        # if this row beats the best so far...
        if score > best_score:
            # remember its score
            best_score = score
            # and remember which row it was
            best_row = i
    # return the row number that looks most like a header
    return best_row


# Helper: re-reads the file using the detected header row as the column names.
def _reread_with_detected_header(uploaded_file, is_excel):
    # read the whole file with NO header, so every row is treated as plain data
    if is_excel:
        raw = pd.read_excel(uploaded_file, header=None)
    else:
        raw = pd.read_csv(uploaded_file, header=None)

    # find which row is really the header
    header_row = _detect_header_row(raw)
    # take that row's values as the list of column names
    header = raw.iloc[header_row].tolist()
    # the real data starts on the row after the header; reset the row numbers to 0, 1, 2...
    df = raw.iloc[header_row + 1:].reset_index(drop=True)
    # give the table its proper column names
    df.columns = header
    # drop columns that are completely empty
    df = df.dropna(axis=1, how='all')
    # drop rows that are completely empty, then reset the row numbers again
    df = df.dropna(axis=0, how='all').reset_index(drop=True)
    # return the cleaned-up table
    return df


# Helper: makes sure every column name is unique (two columns can't share a name).
def make_unique_columns(columns):
    # 'seen' counts how many times we have met each name
    seen = {}
    # 'result' is the new list of names we are building
    result = []
    # go through the names one by one
    for col in columns:
        # turn the name into text, trim spaces, and use "Unnamed" if it is blank
        col = str(col).strip() or "Unnamed"
        # if we have not met this name before...
        if col not in seen:
            # record it with a count of 0
            seen[col] = 0
            # keep the name as it is
            result.append(col)
        # if we HAVE met this name before...
        else:
            # increase the count
            seen[col] += 1
            # add a number to the end, e.g. "Sales_1", so the name becomes unique
            result.append(f"{col}_{seen[col]}")
    # return the list of unique names
    return result


# Reads a saved dataset back from the database and rebuilds it as a pandas table.
def get_dataset_dataframe(dataset):
    # ask the database for this dataset's rows, in order, and only the 'data' field of each
    rows = (
        DataRow.objects
        .filter(dataset=dataset)
        .order_by('row_number')
        .values_list('data', flat=True)
    )
    # keep only the rows that are dictionaries (one dictionary = one row of the table)
    data = [r for r in rows if isinstance(r, dict)]
    # build a table from them, or an empty table if there is no data
    return pd.DataFrame(data) if data else pd.DataFrame()


# Splits the columns into "numeric" (numbers) and "categorical" (text/labels).
def detect_column_types(df):
    # two empty lists to fill in
    numeric, categorical = [], []
    # check each column
    for col in df.columns:
        # if pandas already sees this column as numbers...
        if pd.api.types.is_numeric_dtype(df[col]):
            # it is numeric
            numeric.append(col)
        # otherwise try converting to numbers; if at least 80% convert OK...
        elif pd.to_numeric(df[col], errors='coerce').notna().mean() >= 0.8:
            # treat it as numeric too (numbers stored as text)
            numeric.append(col)
        # everything else is categorical
        else:
            categorical.append(col)
    # return both lists
    return numeric, categorical


# a pattern that matches words that suggest time, such as "date", "year" or "month"
TIME_LIKE_PATTERN = re.compile(r'\b(?:date|year|month|week|quarter|period|day|time|timestamp)\b')


# Helper: True if a column name looks time-related.
def _is_time_like_name(name):
    # lowercase the name, turn underscores into spaces, then search for the pattern
    return bool(TIME_LIKE_PATTERN.search(str(name).lower().replace('_', ' ')))


# Builds a small "info card" about every column (type, number of unique values, time-like or not).
# The Analysis page's JavaScript uses this to recommend a chart type.
def build_column_meta(df, numeric_columns, categorical_columns):
    # the dictionary we will fill in
    meta = {}
    # for each numeric column...
    for col in numeric_columns:
        # store its info
        meta[col] = {
            # mark it as numeric
            'type': 'numeric',
            # how many different values it has (ignoring blanks)
            'unique': int(df[col].nunique(dropna=True)),
            # does its name look like a date/time field?
            'time_like': _is_time_like_name(col),
        }
    # same for each categorical column
    for col in categorical_columns:
        meta[col] = {
            'type': 'categorical',
            'unique': int(df[col].nunique(dropna=True)),
            'time_like': _is_time_like_name(col),
        }
    # return the finished dictionary
    return meta


# Turns the first rows of the table into HTML so the page can show a "View Data" preview.
def build_data_preview(df, max_rows=DATA_PREVIEW_MAX_ROWS):
    # nothing to preview if the table is empty
    if df.empty:
        return None
    # keep only the first max_rows rows
    preview_df = df.head(max_rows)
    # convert to an HTML <table>
    return preview_df.to_html(
        # CSS classes so the table gets styled by the page
        classes='data-table preview-table',
        # no border around the table
        border=0,
        # don't print the row numbers
        index=False,
        # show a dash for empty cells
        na_rep='—',
        # make special characters safe to display (prevents HTML injection)
        escape=True,
    )


# ------------------------------------------------------------
# LANDING
# ------------------------------------------------------------

# The view for the home (landing) page.
def landing(request):
    # just show the landing page template
    return render(request, 'dashboard/landing.html')


# ------------------------------------------------------------
# AUTHENTICATION
# ------------------------------------------------------------

# The view for the Sign Up page.
def signup(request):
    # if the user is only opening the page (not submitting the form)...
    if request.method != 'POST':
        # show an empty sign-up form
        return render(request, 'dashboard/signup.html', {'form': UserCreationForm()})

    # the user submitted the form, so fill the form with what they typed
    form = UserCreationForm(request.POST)
    # check that the username/password follow the rules
    if form.is_valid():
        # create the new user account in the database
        user = form.save()
        # log them in right away so they don't have to type it all again
        auth_login(request, user)  # sign them in immediately after creating the account
        # send them to the landing page
        return redirect('landing')

    # the form had errors, so show it again with the error messages
    return render(request, 'dashboard/signup.html', {'form': form})


# ------------------------------------------------------------
# IMPORT
# ------------------------------------------------------------

# The view for the Import page. Only logged-in users may open it.
@login_required
def import_data(request):
    # if the user is just opening the page, show the upload form
    if request.method != 'POST':
        return render(request, 'dashboard/import.html')

    # get the uploaded file from the form (the input is named "file")
    uploaded_file = request.FILES.get('file')
    # if no file was chosen...
    if not uploaded_file:
        # show the page again with an error message
        return render(request, 'dashboard/import.html', {'error': 'Please select a file.'})

    # 'try' runs the code below; if anything goes wrong, 'except' catches the error
    try:
        # read the file into a table
        df = read_uploaded_file(uploaded_file)
        # if the table has no rows...
        if df.empty:
            # show an error
            return render(request, 'dashboard/import.html', {'error': 'The uploaded dataset is empty.'})

        # make sure all column names are unique
        df.columns = make_unique_columns(df.columns)

        # 'atomic' means: do all database steps together, or undo everything if one fails
        with transaction.atomic():
            # Each user only ever has one active dataset — replacing theirs
            # never touches other users' datasets.
            # delete the user's old dataset (only theirs)
            Dataset.objects.filter(user=request.user).delete()
            # create a new Dataset record that stores the file name and the file itself
            dataset = Dataset.objects.create(user=request.user, name=uploaded_file.name, file=uploaded_file)

            # turn the table into a list of dictionaries (one dictionary per row)
            records = df.to_dict(orient='records')
            # make one DataRow object per row, numbered starting from 1
            rows = [
                DataRow(dataset=dataset, row_number=i + 1, data=_json_safe_record(record))
                for i, record in enumerate(records)
            ]
            # save all rows to the database, 1000 at a time (faster than one by one)
            DataRow.objects.bulk_create(rows, batch_size=1000)

        # A fresh dataset invalidates any previously queued report charts.
        # empty the report queue because the old charts belonged to the old data
        request.session[REPORT_SESSION_KEY] = []

        # go straight to the Data Cleaning page
        return redirect('data_cleaning')

    # if anything above failed...
    except Exception as e:
        # show the import page again with the error text
        return render(request, 'dashboard/import.html', {'error': str(e)})


# Helper: converts one row into values the database can store as JSON (no NaN, numpy types, etc.).
def _json_safe_record(record):
    # the clean dictionary we will build
    safe = {}
    # go through each column name (key) and its value
    for key, value in record.items():
        # if the value is missing (NaN/None); lists and dicts are skipped from this check
        if pd.isna(value) if not isinstance(value, (list, dict)) else False:
            # store it as None (becomes null in JSON)
            safe[key] = None
        # if it is a pandas date/time...
        elif isinstance(value, (pd.Timestamp,)):
            # store it as text like "2026-10-14T00:00:00"
            safe[key] = value.isoformat()
        # if it is a numpy integer...
        elif isinstance(value, (np.integer,)):
            # convert to a normal Python int
            safe[key] = int(value)
        # if it is a numpy decimal number...
        elif isinstance(value, (np.floating,)):
            # convert to a normal Python float
            safe[key] = float(value)
        # anything else is already safe
        else:
            safe[key] = value
    # return the cleaned dictionary
    return safe


# ------------------------------------------------------------
# DATA CLEANING (duplicates + missing values)
# ------------------------------------------------------------

# how many problem rows to list on the page at most
CLEANING_PREVIEW_LIMIT = 100
# the allowed ways to fill missing values
FILL_STRATEGIES = {'default', 'custom'}


# Helper: treats empty text ("" or spaces) as a real missing value (NaN).
def _normalize_missing(df):
    # work on a copy so the original table is not changed
    df = df.copy()
    # check each column
    for col in df.columns:
        # only text columns need this fix (numeric columns already use NaN)
        if not pd.api.types.is_numeric_dtype(df[col]):
            # for every value: if it is None or blank text, make it NaN; otherwise keep it
            df[col] = df[col].map(
                lambda v: np.nan if v is None or (isinstance(v, str) and not v.strip()) else v
            )
    # return the fixed copy
    return df


# Helper: turns a cell value into clean text for display.
def _cell_text(value):
    # None becomes empty text
    if value is None:
        return ''
    # for decimal numbers...
    if isinstance(value, float):
        # NaN becomes empty text
        if np.isnan(value):
            return ''
        # 5.0 is shown as "5" instead of "5.0"
        if value.is_integer():
            return str(int(value))
            # everything else is just converted to text
    return str(value)


# Helper: picks certain rows (by position) and returns them as (row number, list of cell values).
def _display_rows(df, positions):
    # take only the rows at these positions
    subset = df.iloc[positions]
    # for each column, get its values as a list
    columns = [subset[c].tolist() for c in df.columns]
    # zip the columns together so we get one list of cells per row, paired with the row number
    return [(pos + 1, list(cells)) for pos, cells in zip(positions, zip(*columns))]


# Helper: converts everything to text so rows can be compared reliably for duplicates.
def _text_frame(df):
    # astype(str) makes every value text; fillna('') replaces any leftover blanks
    return df.astype(str).fillna('')


# Checks the table for duplicate rows and missing values, and returns all the results for the page.
def analyze_dataset_quality(df):
    # how many rows the table has
    total_rows = len(df)
    # the list of column names
    columns = list(df.columns)

    # ---- Duplicates ----
    # a text version of the table, for comparing rows
    df_str = _text_frame(df)
    # True for every row that is a copy of an EARLIER row (the first copy is not flagged)
    dup_mask = df_str.duplicated(keep='first')
    # count how many duplicate rows there are
    duplicate_count = int(dup_mask.sum())
    # the list of duplicate rows we will show on the page
    duplicate_rows = []

    # only do the extra work if duplicates exist
    if duplicate_count:
        # give each distinct row an id number; identical rows get the same id
        group_ids = df_str.groupby(columns, sort=False).ngroup().tolist()
        # will remember where each distinct row first appeared
        first_position = {}
        # go through the ids in order
        for pos, gid in enumerate(group_ids):
            # record the position only the first time we see an id
            first_position.setdefault(gid, pos)

        # positions of the duplicate rows (limited to the preview limit)
        dup_positions = [i for i, flag in enumerate(dup_mask.tolist()) if flag][:CLEANING_PREVIEW_LIMIT]
        # for each duplicate row...
        for row_no, cells in _display_rows(df, dup_positions):
            # add an entry the template can display
            duplicate_rows.append({
                # the row number of the duplicate
                'row': row_no,
                # the row number of the original it copies
                'duplicate_of': first_position[group_ids[row_no - 1]] + 1,
                # the cell values as text
                'cells': [_cell_text(c) for c in cells],
            })

    # ---- Missing values ----
    # a table of True/False: True where a cell is empty
    isna = df.isna()
    # total number of empty cells
    missing_cell_count = int(isna.to_numpy().sum())
    # True for each row that has at least one empty cell
    missing_row_mask = isna.any(axis=1)
    # how many rows have a missing value
    missing_row_count = int(missing_row_mask.sum())

    # info for each column that has missing values
    missing_columns = []
    # the first column is used as a label to help the user find the row
    id_col = columns[0]
    # the values of that first column
    id_values = df[id_col].tolist()
    # check each column
    for col in columns:
        # how many empty cells this column has
        n = int(isna[col].sum())
        # only continue if there is at least one
        if n:
            # the list of empty cells in this column
            cells = []
            # go through the positions of the empty cells (limited to the preview limit)
            for pos in np.flatnonzero(isna[col].to_numpy())[:CLEANING_PREVIEW_LIMIT]:
                # make sure it is a normal int
                pos = int(pos)
                # the label text, e.g. the student's ID in that row
                id_text = _cell_text(id_values[pos]) if col != id_col else ''
                # add the cell's info
                cells.append({
                    # the row number
                    'row': pos + 1,
                    # a label such as "Student ID: 12"
                    'label': f"{id_col}: {id_text}" if id_text else '',
                })
            # add this column's summary
            missing_columns.append({
                # a number used to name the form inputs on the page
                'index': len(missing_columns),
                # the column name
                'name': col,
                # how many missing cells
                'count': n,
                # the percentage of rows that are missing, rounded to 1 decimal place
                'percent': round(n / total_rows * 100, 1) if total_rows else 0,
                # is this a numeric column?
                'is_numeric': bool(pd.api.types.is_numeric_dtype(df[col])),
                # the list of missing cells
                'cells': cells,
            })

    # the rows (with missing values) to display in a table
    missing_rows = []
    # only if there are any
    if missing_row_count:
        # positions of rows with missing values (limited to the preview limit)
        missing_positions = [i for i, flag in enumerate(missing_row_mask.tolist()) if flag][:CLEANING_PREVIEW_LIMIT]
        # for each such row...
        for row_no, cells in _display_rows(df, missing_positions):
            # which cells in this row are empty (a list of True/False)
            flags = isna.iloc[row_no - 1].tolist()
            # add the row so the template can highlight the empty cells
            missing_rows.append({
                'row': row_no,
                'cells': [{'text': _cell_text(c), 'missing': bool(m)} for c, m in zip(cells, flags)],
            })

    # send all the results back as one dictionary
    return {
        # True when there are no duplicates and no missing values
        'is_clean': duplicate_count == 0 and missing_cell_count == 0,
        'duplicate_count': duplicate_count,
        'duplicate_rows': duplicate_rows,
        'missing_cell_count': missing_cell_count,
        'missing_row_count': missing_row_count,
        'missing_columns': missing_columns,
        'missing_rows': missing_rows,
    }


# Helper: loads the current user's newest dataset and returns (dataset, table).
def _load_user_dataset(request):
    # get the user's most recently uploaded dataset (or None)
    dataset = Dataset.objects.filter(user=request.user).order_by('-uploaded_at').first()
    # if the user has none...
    if not dataset:
        # return two Nones
        return None, None
    # return the dataset and its table (with blank text turned into NaN)
    return dataset, _normalize_missing(get_dataset_dataframe(dataset))


# Helper: saves a cleaned table back to the database, replacing the old rows.
def _save_cleaned_dataframe(dataset, df):
    # renumber the rows 0, 1, 2... after rows were removed
    df = df.reset_index(drop=True)

    # columns that only hold whole numbers (stored as decimals like 5.0)
    int_like = set()
    # check each column
    for col in df.columns:
        # only decimal (float) columns can be "whole numbers in disguise"
        if pd.api.types.is_float_dtype(df[col]):
            # ignore empty cells
            non_null = df[col].dropna()
            # if there are values and every one is a whole number (no .5 etc.)...
            if len(non_null) and bool((non_null % 1 == 0).all()):
                # remember this column so we can save it as integers
                int_like.add(col)

    # the new DataRow objects to save
    rows = []
    # go through each row of the table
    for i, record in enumerate(df.to_dict(orient='records')):
        # make the row safe for the database
        safe = _json_safe_record(record)
        # for the whole-number columns...
        for col in int_like:
            # if the cell is not empty, store it as an int (5 instead of 5.0)
            if safe.get(col) is not None:
                safe[col] = int(safe[col])
        # create the row object, numbered from 1
        rows.append(DataRow(dataset=dataset, row_number=i + 1, data=safe))

    # do the delete and the re-save together, so we never end up with half the data
    with transaction.atomic():
        # delete the dataset's old rows
        DataRow.objects.filter(dataset=dataset).delete()
        # save the new rows
        DataRow.objects.bulk_create(rows, batch_size=1000)


# The view for the Data Cleaning page.
@login_required
def data_cleaning(request):
    # how many charts are queued in the report (shown as a badge in the navbar)
    report_count = len(request.session.get(REPORT_SESSION_KEY, []))
    # load the user's dataset
    dataset, df = _load_user_dataset(request)

    # if there is no dataset (or it is empty)...
    if dataset is None or df.empty:
        # show the page with a "no dataset" message
        return render(request, 'dashboard/cleaning.html', {'no_dataset': True, 'report_count': report_count})

    # the basic info the page needs
    context = {
        'dataset': dataset,
        'row_count': len(df),
        'column_count': len(df.columns),
        'columns': list(df.columns),
        'preview_limit': CLEANING_PREVIEW_LIMIT,
        'report_count': report_count,
    }
    # add the duplicate/missing-value results to that info
    context.update(analyze_dataset_quality(df))
    # show the page
    return render(request, 'dashboard/cleaning.html', context)


# The view that removes duplicate rows (runs when the user clicks "Remove Duplicates").
@login_required
def clean_remove_duplicates(request):
    # this action only works for form submissions (POST); otherwise go back
    if request.method != 'POST':
        return redirect('data_cleaning')

    # load the data
    dataset, df = _load_user_dataset(request)
    # if there is nothing to clean, send the user to the Import page
    if dataset is None or df.empty:
        return redirect('import_data')

    # True for every duplicate row
    dup_mask = _text_frame(df).duplicated(keep='first')
    # how many will be removed
    removed = int(dup_mask.sum())
    # if there are any...
    if removed:
        # keep only the rows that are NOT duplicates (~ means "not") and save them
        _save_cleaned_dataframe(dataset, df[~dup_mask])
        # show a green success message
        messages.success(
            request,
            f"Removed {removed:,} duplicate row{'' if removed == 1 else 's'}. "
            f"Only the first copy of each row was kept."
        )
    # if there were none...
    else:
        # show an info message
        messages.info(request, "No duplicate rows were found.")
    # go back to the cleaning page
    return redirect('data_cleaning')


# The view that deletes every row that has a missing value (Option 1 on the page).
@login_required
def clean_drop_missing(request):
    # only form submissions are allowed
    if request.method != 'POST':
        return redirect('data_cleaning')

    # load the data
    dataset, df = _load_user_dataset(request)
    # nothing to clean? go to Import
    if dataset is None or df.empty:
        return redirect('import_data')

    # True for each row that has at least one empty cell
    missing_mask = df.isna().any(axis=1)
    # how many such rows
    removed = int(missing_mask.sum())

    # if no row has missing values...
    if not removed:
        # tell the user
        messages.info(request, "No rows with missing values were found.")
    # if EVERY row has a missing value...
    elif removed == len(df):
        # refuse, because deleting them all would leave an empty dataset
        messages.error(
            request,
            "Every row has at least one missing value, so excluding them would leave no data. "
            "Try filling the missing values instead."
        )
    # otherwise it is safe to delete
    else:
        # keep only the rows WITHOUT missing values, and save
        _save_cleaned_dataframe(dataset, df[~missing_mask])
        # tell the user what happened
        messages.success(
            request,
            f"Excluded {removed:,} row{'' if removed == 1 else 's'} with missing values."
        )
    # go back to the cleaning page
    return redirect('data_cleaning')


# The view that fills in missing values (Option 2 on the page).
@login_required
def clean_fill_missing(request):
    # only form submissions are allowed
    if request.method != 'POST':
        return redirect('data_cleaning')

    # load the data
    dataset, df = _load_user_dataset(request)
    # nothing to clean? go to Import
    if dataset is None or df.empty:
        return redirect('import_data')

    # counters: cells filled, columns filled, and columns where a typed number was invalid
    filled_cells, filled_columns, bad_number_columns = 0, 0, []
    # the list of column names (used to find a column's position)
    column_names = list(df.columns)

    # the form sends fields named like "col__0"; look through all submitted fields
    for key, column in request.POST.items():
        # skip fields that are not column names
        if not key.startswith('col__'):
            continue
        # get the number part after "col__"
        idx = key[len('col__'):]
        # find what the user chose for this column: "", "default" or "custom"
        strategy = request.POST.get(f'strategy__{idx}', '')
        # skip if the choice is invalid or the column doesn't exist
        if strategy not in FILL_STRATEGIES or column not in column_names:
            continue

        # is this a numeric column?
        is_numeric = pd.api.types.is_numeric_dtype(df[column])
        # how many cells we filled in this column
        n_filled = 0

        # "default" fills EVERY empty cell in the column
        if strategy == 'default':
            # count the empty cells
            n_filled = int(df[column].isna().sum())
            # if there are any...
            if n_filled:
                # fill with 0 for numbers, or "Unknown" for text
                df[column] = df[column].fillna(0 if is_numeric else 'Unknown')
        # "custom" fills only the cells the user typed a value for
        else:
            # the position of this column in the table
            col_pos = column_names.index(column)
            # the typed boxes are named like "cell__0__5" (column 0, row 5)
            prefix = f'cell__{idx}__'
            # becomes True if the user typed something that is not a number
            had_bad_number = False

            # look at every submitted field again
            for cell_key, text in request.POST.items():
                # skip fields that don't belong to this column's custom boxes
                if not cell_key.startswith(prefix):
                    continue
                # remove spaces around what the user typed
                text = text.strip()
                # a blank box means "leave this cell missing"
                if text == '':
                    continue
                    # get the row number from the field name
                try:
                    # row numbers on the page start at 1, but positions start at 0
                    pos = int(cell_key[len(prefix):]) - 1
                # if it is not a valid number, skip it
                except ValueError:
                    continue

                # skip if the row doesn't exist, or the cell is not actually empty
                if not (0 <= pos < len(df)) or not pd.isna(df.iat[pos, col_pos]):
                    continue

                # for numeric columns, the typed value must be a number
                if is_numeric:
                    try:
                        # convert the text to a number
                        value = float(text)
                    # not a number, so remember the problem and skip this cell
                    except ValueError:
                        had_bad_number = True
                        continue
                # for text columns, use the typed text as it is
                else:
                    value = text

                # write the value into that cell
                df.iat[pos, col_pos] = value
                # count it
                n_filled += 1

            # if any typed value was invalid, remember this column for a warning message
            if had_bad_number:
                bad_number_columns.append(column)

        # if this column got at least one filled cell...
        if n_filled:
            # add to the totals
            filled_cells += n_filled
            filled_columns += 1

    # if anything was filled...
    if filled_cells:
        # save the updated table
        _save_cleaned_dataframe(dataset, df)
        # show a success message
        messages.success(
            request,
            f"Filled {filled_cells:,} missing value{'' if filled_cells == 1 else 's'} "
            f"across {filled_columns} column{'' if filled_columns == 1 else 's'}."
        )
    # if some typed values were not valid numbers...
    if bad_number_columns:
        # show a warning that lists the affected columns
        messages.warning(
            request,
            "Some values in " + ", ".join(bad_number_columns) +
            " weren't valid numbers, so those cells were left empty."
        )
    # if nothing was filled and there were no bad numbers...
    if not filled_cells and not bad_number_columns:
        # remind the user to choose an option
        messages.warning(
            request,
            "Nothing was filled. Choose Default, or type custom values, for at least one column."
        )

    # go back to the cleaning page
    return redirect('data_cleaning')


# ------------------------------------------------------------
# FINANCIAL RATIO ANALYSIS (Profit & Loss / Balance Sheet)
# ------------------------------------------------------------

# For each financial field, a list of column-name keywords to look for in the user's data.
# This only kicks in when a dataset looks like a financial statement.
FINANCIAL_KEYWORDS = {
    # words that mean "revenue"
    'revenue': ['total revenue', 'net revenue', 'net sales', 'total sales', 'revenue', 'sales'],
    # cost of goods sold
    'cogs': ['cost of goods sold', 'cost of sales', 'cogs'],
    'gross_profit': ['gross profit'],
    'operating_income': ['operating income', 'operating profit', 'ebit'],
    'net_income': ['net income', 'net profit', 'profit after tax', 'profit for the year'],
    'total_assets': ['total assets'],
    'current_assets': ['total current assets', 'current assets'],
    'inventory': ['inventories', 'inventory'],
    'total_liabilities': ['total liabilities'],
    'current_liabilities': ['total current liabilities', 'current liabilities'],
    'total_equity': [
        "total shareholders' equity", 'total stockholders equity', 'total equity',
        "shareholders' equity", 'stockholders equity',
    ],
}

# For each ratio: a list of (minimum value, plain-English meaning), checked from best to worst.
RATIO_BENCHMARKS = {
    'Current Ratio': [(2, 'strong liquidity'), (1, 'adequate liquidity'), (0, 'potential liquidity risk')],
    'Quick Ratio': [(1, 'healthy short-term liquidity'), (0.5, 'moderate liquidity'), (0, 'tight liquidity')],
    'Debt-to-Equity': [(2, 'high leverage'), (1, 'moderate leverage'), (0, 'low leverage')],
    'Gross Margin %': [(40, 'strong pricing power'), (20, 'moderate margins'), (0, 'thin margins')],
    'Operating Margin %': [(15, 'strong operating efficiency'), (5, 'moderate efficiency'),
                           (0, 'weak operating efficiency')],
    'Net Margin %': [(15, 'strong profitability'), (5, 'moderate profitability'), (0, 'weak/negative profitability')],
    'ROA %': [(10, 'strong asset returns'), (3, 'moderate asset returns'), (0, 'weak asset returns')],
    'ROE %': [(15, 'strong shareholder returns'), (5, 'moderate shareholder returns'), (0, 'weak shareholder returns')],
}


# Matches the user's column names to the financial fields above (e.g. "Net Sales" -> revenue).
def map_financial_columns(columns):
    # a lowercase version of each column name, to match keywords more easily
    normalized = {c: str(c).strip().lower() for c in columns}
    # the list of possible matches: (keyword length, field, column)
    candidates = []
    # go through every field and its keywords
    for field, keywords in FINANCIAL_KEYWORDS.items():
        for kw in keywords:
            for col, norm in normalized.items():
                # if the keyword appears inside the column name, it is a candidate match
                if kw in norm:
                    candidates.append((len(kw), field, col))
    # try the longest (most specific) keywords first
    candidates.sort(reverse=True)

    # the final result, and the columns already used
    mapping, used_cols = {}, set()
    for _length, field, col in candidates:
        # skip if this field is already matched or this column is already taken
        if field in mapping or col in used_cols:
            continue
        # accept the match
        mapping[field] = col
        used_cols.add(col)
    # return the finished mapping
    return mapping


# Calculates financial ratios (margins, current ratio, ROA, ROE...) and adds them as new columns.
def add_financial_ratios(df):
    # find which columns match financial fields
    mapping = map_financial_columns(df.columns)
    # if none match, this is not a financial dataset: return the table unchanged
    if not mapping:
        return df, [], False, mapping

    # inner helper: get a field's column as numbers (or None if the field wasn't found)
    def col(field):
        return pd.to_numeric(df[mapping[field]], errors='coerce') if field in mapping else None

    # get each financial field (each is None if missing)
    revenue = col('revenue')
    cogs = col('cogs')
    gross_profit = col('gross_profit')
    operating_income = col('operating_income')
    net_income = col('net_income')
    total_assets = col('total_assets')
    current_assets = col('current_assets')
    inventory = col('inventory')
    total_liabilities = col('total_liabilities')
    current_liabilities = col('current_liabilities')
    total_equity = col('total_equity')

    # if gross profit is missing but we have revenue and cost of goods sold, calculate it
    if gross_profit is None and revenue is not None and cogs is not None:
        gross_profit = revenue - cogs

    # work on a copy so the original table is untouched
    df = df.copy()
    # the names of the ratio columns we add
    ratio_columns = []

    # Gross Margin % = gross profit / revenue x 100 (replace(0, nan) avoids dividing by zero)
    if revenue is not None and gross_profit is not None:
        df['Gross Margin %'] = (gross_profit / revenue.replace(0, np.nan)) * 100
        ratio_columns.append('Gross Margin %')

    # Operating Margin % = operating income / revenue x 100
    if revenue is not None and operating_income is not None:
        df['Operating Margin %'] = (operating_income / revenue.replace(0, np.nan)) * 100
        ratio_columns.append('Operating Margin %')

    # Net Margin % = net income / revenue x 100
    if revenue is not None and net_income is not None:
        df['Net Margin %'] = (net_income / revenue.replace(0, np.nan)) * 100
        ratio_columns.append('Net Margin %')

    # Current Ratio = current assets / current liabilities
    if current_assets is not None and current_liabilities is not None:
        df['Current Ratio'] = current_assets / current_liabilities.replace(0, np.nan)
        ratio_columns.append('Current Ratio')

        # Quick Ratio = (current assets - inventory) / current liabilities
        if inventory is not None:
            df['Quick Ratio'] = (current_assets - inventory) / current_liabilities.replace(0, np.nan)
            ratio_columns.append('Quick Ratio')

        # Working Capital = current assets - current liabilities
        df['Working Capital'] = current_assets - current_liabilities
        ratio_columns.append('Working Capital')

    # Debt-to-Equity = total liabilities / total equity
    if total_liabilities is not None and total_equity is not None:
        df['Debt-to-Equity'] = total_liabilities / total_equity.replace(0, np.nan)
        ratio_columns.append('Debt-to-Equity')

    # ROA % (Return on Assets) = net income / total assets x 100
    if net_income is not None and total_assets is not None:
        df['ROA %'] = (net_income / total_assets.replace(0, np.nan)) * 100
        ratio_columns.append('ROA %')

    # ROE % (Return on Equity) = net income / total equity x 100
    if net_income is not None and total_equity is not None:
        df['ROE %'] = (net_income / total_equity.replace(0, np.nan)) * 100
        ratio_columns.append('ROE %')

    # the dataset counts as "financial" if at least one ratio could be calculated
    is_financial = len(ratio_columns) > 0
    # return the table, the ratio names, the yes/no flag, and the column mapping
    return df, ratio_columns, is_financial, mapping


# Gives a plain-English meaning to a ratio value (e.g. 2.4 current ratio -> "strong liquidity").
def interpret_ratio(name, value):
    # if we have no benchmark for this ratio, or the value is missing, say nothing
    if name not in RATIO_BENCHMARKS or pd.isna(value):
        return None
    # check the benchmarks from best to worst
    for threshold, label in RATIO_BENCHMARKS[name]:
        # the first threshold the value reaches gives the label
        if value >= threshold:
            return label
    # no label matched
    return None


# Writes a short list of sentences describing the latest value of each ratio.
def financial_ratio_narrative(df, ratio_columns):
    # nothing to describe
    if not ratio_columns or df.empty:
        return []
    # use the last row of the table (the most recent period)
    latest = df.iloc[-1]
    # the sentences we will build
    lines = []
    for col in ratio_columns:
        # get this ratio's latest value
        value = latest.get(col)
        # skip if it is missing
        if value is None or pd.isna(value):
            continue
        # get the plain-English meaning
        note = interpret_ratio(col, value)
        # add " — meaning." if there is one, otherwise just "."
        suffix = f" — {note}." if note else "."
        # build the sentence, e.g. "Net Margin %: 12.50 — moderate profitability."
        lines.append(f"{col}: {value:.2f}{suffix}")
    # return the sentences
    return lines


# ------------------------------------------------------------
# CHART BUILDING (returns a matplotlib Figure)
# ------------------------------------------------------------

# Helper: puts line-chart points in a natural order (numbers, then dates, then alphabetical).
def _order_for_line(grouped):
    # the labels (x values) of the data
    idx = grouped.index
    # if the labels are numbers (like years), sort by number
    if pd.api.types.is_numeric_dtype(idx):
        return grouped.sort_index()
    # otherwise try to read the labels as dates (silencing any warnings)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        parsed = pd.to_datetime(pd.Series(idx.astype(str), index=idx), errors='coerce')
    # if EVERY label is a valid date, sort by date
    if parsed.notna().all():
        return grouped.loc[parsed.sort_values().index]
    # otherwise sort alphabetically
    return grouped.sort_index()


# The main chart maker. It draws the chart the user chose and returns the matplotlib figure.
def build_chart(df, chart_type, x_column, y_column, color_column, matrix_columns, numeric_columns, agg='sum', date_interval='original'):
    # ---- Scatter matrix (a grid of scatter plots) ----
    if chart_type == 'scatter_matrix':
        # use the chosen numeric columns (max 4); if none chosen, use the first 4 numeric columns
        cols = [c for c in matrix_columns if c in numeric_columns][:4] or numeric_columns[:4]
        # we need at least 2 columns to compare
        if len(cols) < 2:
            raise ValueError("Select at least two numeric columns for a scatter matrix.")
        # keep just those columns as numbers and drop rows with blanks
        sample = df[cols].apply(pd.to_numeric, errors='coerce').dropna()
        # if there are too many rows, use a random sample so the chart stays fast
        if len(sample) > MAX_POINTS:
            sample = sample.sample(MAX_POINTS, random_state=42)
        # draw the grid of scatter plots
        axes = pd.plotting.scatter_matrix(sample, figsize=(8, 8), alpha=0.5)
        # return the figure that holds the grid
        return axes[0][0].get_figure()

    # for every other chart: create an empty figure (fig) with one drawing area (ax)
    fig, ax = plt.subplots(figsize=(8, 5))

    # ---- Bar, line, and pie charts group records by the X column ----
    if chart_type in ('bar', 'line', 'pie'):
        # For line charts, optionally turn a date column into day/month/quarter/year labels.
        group_values = df[x_column]
        if chart_type == 'line' and date_interval != 'original':
            parsed_dates = pd.to_datetime(df[x_column], errors='coerce')
            if parsed_dates.notna().any():
                if date_interval == 'day':
                    group_values = parsed_dates.dt.strftime('%Y-%m-%d')
                elif date_interval == 'month':
                    group_values = parsed_dates.dt.to_period('M').astype(str)
                elif date_interval == 'quarter':
                    group_values = parsed_dates.dt.to_period('Q').astype(str)
                elif date_interval == 'year':
                    group_values = parsed_dates.dt.year.astype('Int64').astype(str)
                group_values = group_values.where(parsed_dates.notna(), other=None)
        # Total means number of rows in each group; Count means non-empty Y values.
        if agg == 'total' or y_column == '__count__':
            grouped = group_values.groupby(group_values).size()
        elif agg == 'count':
            grouped = df[y_column].groupby(group_values).count()
        elif agg == 'nunique':
            grouped = df[y_column].groupby(group_values).nunique(dropna=True)
        else:
            numeric_y = pd.to_numeric(df[y_column], errors='coerce')
            grouped = numeric_y.groupby(group_values).agg(agg).dropna()
        # Sort line charts chronologically; other chart types rank largest first.
        if chart_type == 'line':
            grouped = _order_for_line(grouped)
        else:
            grouped = grouped.sort_values(ascending=False)
        # if nothing could be calculated, stop with a clear message
        if grouped.empty:
            raise ValueError(
                f"'{y_column}' has no numeric values that could be calculated by '{x_column}'."
            )
        # for line charts, put the points in their natural order
        if chart_type == 'line':
            grouped = _order_for_line(grouped)  # follow the x values' natural order
            # if the labels are whole numbers (like years)...
            if pd.api.types.is_numeric_dtype(grouped.index) and (grouped.index.to_numpy(dtype=float) % 1 == 0).all():
                # whole numbers such as years: label every point instead of an axis showing 2020.5
                grouped = grouped.rename(index=lambda v: str(int(v)))
        # show at most MAX_CATEGORIES groups
        grouped = grouped.head(MAX_CATEGORIES)
        # draw a bar chart in teal
        if chart_type == 'bar':
            grouped.plot(kind='bar', ax=ax, color='#0f766e')
        # draw a line chart with round markers
        elif chart_type == 'line':
            grouped.plot(kind='line', marker='o', ax=ax, color='#0f766e')
        # draw a pie chart
        else:
            grouped.plot(kind='pie', ax=ax, ylabel='')
        # build the chart title text
        # (the first word is Total, Average, Median... depending on the calculation the user picked)
        label = ('Total Records' if agg == 'total' else 'Count' if agg == 'count' or y_column == '__count__' else f"{AGG_LABELS[agg]} {y_column}")
        # put the title on the chart
        ax.set_title(f"{label} by {x_column}")

    # ---- Scatter plot: one dot per record ----
    elif chart_type == 'scatter':
        # make a small table with the X and Y values as numbers, dropping rows with blanks
        points = pd.DataFrame({
            'x': pd.to_numeric(df[x_column], errors='coerce'),
            'y': pd.to_numeric(df[y_column], errors='coerce'),
        }).dropna()
        # use a random sample if there are too many dots
        if len(points) > MAX_POINTS:
            points = points.sample(MAX_POINTS, random_state=42)
        # draw the dots (alpha=0.5 makes them see-through so overlaps are visible)
        ax.scatter(points['x'], points['y'], alpha=0.5, color='#0f766e')
        # label the axes
        ax.set_xlabel(x_column)
        ax.set_ylabel(y_column)
        # set the title
        ax.set_title(f"{y_column} vs {x_column}")

    # ---- Colored scatter: a scatter plot with one color per category ----
    elif chart_type == 'colored_scatter':
        # handle each category (group) separately
        for category, group in df.groupby(color_column):
            # numeric X and Y for this group, dropping blanks
            pts = pd.DataFrame({
                'x': pd.to_numeric(group[x_column], errors='coerce'),
                'y': pd.to_numeric(group[y_column], errors='coerce'),
            }).dropna()
            # sample if there are too many dots
            if len(pts) > MAX_POINTS:
                pts = pts.sample(MAX_POINTS, random_state=42)
            # draw this group's dots; the label is used in the legend
            ax.scatter(pts['x'], pts['y'], alpha=0.5, label=str(category))
        # label the axes
        ax.set_xlabel(x_column)
        ax.set_ylabel(y_column)
        # show the color legend
        ax.legend()
        # set the title
        ax.set_title(f"{y_column} vs {x_column} by {color_column}")

    # ---- Distribution: a histogram of one numeric column ----
    elif chart_type == 'distribution':
        # the column as numbers, with blanks removed
        values = pd.to_numeric(df[y_column], errors='coerce').dropna()
        # draw a histogram with 20 bars (bins)
        ax.hist(values, bins=20, color='#0f766e')
        # set the title
        ax.set_title(f"Distribution of {y_column}")

    # if the chart type is none of the above, it is not valid
    else:
        raise ValueError("Unknown chart type.")

    # return the finished figure
    return fig


# ------------------------------------------------------------
# RECOMMENDED CHARTS (a guide to what is worth plotting for this dataset)
# ------------------------------------------------------------

# the friendly display name of each chart type
CHART_TYPE_NAMES = {
    'bar': 'Bar Chart', 'line': 'Line Chart', 'pie': 'Pie Chart',
    'scatter': 'Scatter Plot', 'colored_scatter': 'Colored Scatter',
    'distribution': 'Distribution', 'scatter_matrix': 'Scatter Matrix',
}

# Column names that suggest money / counts - the ones that make sense to add up.
PRIORITY_KEYWORDS = (
    'revenue', 'sales', 'profit', 'income', 'amount', 'cost', 'expense', 'balance',
    'total', 'units', 'quantity', 'qty', 'principal', 'payment', 'repay', 'loan',
)
# Column names that should NOT be added up (percentages, rates, prices, scores...).
NON_ADDITIVE_PATTERN = re.compile(
    r'%|\b(?:rate|ratio|rating|score|percent|pct|price|age|days?|year|month)\b'
)
# a pattern that matches names like "id", "Order ID", "customer_id"
ID_PATTERN = re.compile(r'(?:^|[^a-z])id(?:[^a-z]|$)')


# Helper: lowercase a column name and turn underscores into spaces.
def _normalized_name(name):
    return str(name).lower().replace('_', ' ')


# Helper: guesses whether a column is just an ID number (not useful to chart).
def _is_id_like(name, series):
    """Row identifiers (Order ID, Customer ID...) are never worth charting."""
    # if the NAME looks like an id, it is one
    if ID_PATTERN.search(_normalized_name(name)):
        return True
    # ignore blank cells
    non_null = series.dropna()
    # for longer numeric columns, check if the values look like 1, 2, 3, 4...
    if len(non_null) > 20 and pd.api.types.is_numeric_dtype(series):
        try:
            # are all values whole numbers?
            whole = bool((non_null % 1 == 0).all())
        # if the check can't run, assume it is not an id
        except TypeError:
            return False
        # whole numbers, all different, and always going up or always going down = an ID column
        if whole and non_null.nunique() == len(non_null) and (
                non_null.is_monotonic_increasing or non_null.is_monotonic_decreasing
        ):
            return True
    # otherwise it is not an id
    return False


# Helper: True if the column's values have a natural order (numbers, or text that are dates).
def _is_orderable(series):
    """True if the values have a natural order (numbers, or text that parses as dates)."""
    # numbers can always be ordered
    if pd.api.types.is_numeric_dtype(series):
        return True
    # ignore blank cells
    non_null = series.dropna()
    # an empty column has no order
    if non_null.empty:
        return False
    # try reading every value as a date (silencing warnings)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        parsed = pd.to_datetime(non_null.astype(str), errors='coerce')
    # orderable only if EVERY value is a valid date
    return bool(parsed.notna().all())


# Builds the "Recommended Charts" list shown in the pop-up on the Analysis page.
def build_chart_recommendations(df, numeric_columns, categorical_columns, column_meta, ratio_columns=()):
    """Concrete chart ideas, built from this dataset's own columns, shown on the
    Analysis page as a guide. Each item has a short description and a query
    string that pre-fills the chart controls."""
    # the number of rows
    total_rows = len(df)
    # no rows means no recommendations
    if not total_rows:
        return []

    # a set of the financial ratio column names (empty for normal datasets)
    ratio_set = set(ratio_columns or ())

    # inner helper: get the info card of one column
    def meta(col):
        return column_meta.get(col, {})

    # inner helper: columns with money-like names get priority 0 (shown first), others get 1
    def priority(col):
        name = _normalized_name(col)
        return 0 if any(k in name for k in PRIORITY_KEYWORDS) else 1

    # Numeric columns worth analysing: no IDs, no year/month-style columns.
    analysis_cols = [
        c for c in numeric_columns
        if not meta(c).get('time_like')
           and meta(c).get('unique', 0) >= 2
           and not _is_id_like(c, df[c])
    ]
    # put the most important columns first
    analysis_cols.sort(key=priority)

    # Numeric columns that make sense to add up (money, units, counts).
    metric_cols = (
            [c for c in analysis_cols if c not in ratio_set and not NON_ADDITIVE_PATTERN.search(_normalized_name(c))]
            or [c for c in analysis_cols if c not in ratio_set]
            or analysis_cols
    )

    # Time-like columns (with a natural order) for line charts.
    time_cols = [
        c for c in list(categorical_columns) + list(numeric_columns)
        if meta(c).get('time_like')
                   and 3 <= meta(c).get('unique', 0) <= MAX_CATEGORIES
                   and _is_orderable(df[c])
    ]

    # Category columns to group by.
    group_cols = [
        c for c in categorical_columns
        if c not in time_cols
           and 2 <= meta(c).get('unique', 0) <= MAX_CATEGORIES
           and meta(c).get('unique', 0) < total_rows
           and not ID_PATTERN.search(_normalized_name(c))
    ]
    # put columns with 3-15 groups first, then those with fewer groups
    group_cols.sort(key=lambda c: (0 if 3 <= meta(c)['unique'] <= 15 else 1, meta(c)['unique']))

    # the list of recommendations we will build
    recs = []

    # inner helper: adds one recommendation to the list
    def add(chart_type, title, description, params):
        recs.append({
            # which chart type it is
            'chart_type': chart_type,
            # its friendly name
            'type_label': CHART_TYPE_NAMES[chart_type],
            # the recommendation title
            'title': title,
            # the explanation sentence
            'description': description,
            # the URL query that pre-fills the chart controls when the user clicks "Create this chart"
            'query': urlencode({'chart_type': chart_type, **params}, doseq=True),
        })

    # --- Bar: compare totals across groups ---
    if group_cols:
        # use the best grouping column
        g = group_cols[0]
        # pair it with up to two metric columns
        bar_pairs = [(g, m) for m in metric_cols[:2]]
        # if we only have one pair, add a second grouping column for variety
        if len(bar_pairs) < 2 and len(group_cols) > 1 and metric_cols:
            bar_pairs.append((group_cols[1], metric_cols[0]))
        # add a bar recommendation for each pair
        for gg, m in bar_pairs:
            add('bar', f"Total {m} by {gg}",
                f"Compares total {m} across the {meta(gg)['unique']} {gg} groups, showing which lead and which trail.",
                {'x_column': gg, 'y_column': m})
        # also recommend counting the records per group
        add('bar', f"Number of records by {g}",
            f"Counts how many records fall into each of the {meta(g)['unique']} {g} groups.",
            {'x_column': g, 'y_column': '__count__'})

    # --- Pie: share of a total (only for a handful of categories) ---
    # pies only work well with 6 or fewer slices
    pie_groups = [c for c in group_cols if meta(c)['unique'] <= 6]
    if pie_groups and metric_cols:
        g, m = pie_groups[0], metric_cols[0]
        add('pie', f"Share of total {m} by {g}",
            f"Shows each {g}'s share of total {m} across {meta(g)['unique']} slices.",
            {'x_column': g, 'y_column': m})

    # --- Line: trend over time ---
    if time_cols and metric_cols:
        t = time_cols[0]
        for m in metric_cols[:2]:
            add('line', f"Trend of total {m} by {t}",
                f"Follows total {m} across {t} to reveal growth, decline or seasonal patterns.",
                {'x_column': t, 'y_column': m})

    # --- Scatter: pairs of numeric columns that move together ---
    # list of (strength, correlation, column A, column B)
    pairs = []
    # check at most 12 columns, so this stays fast
    corr_cols = analysis_cols[:12]
    if len(corr_cols) >= 2:
        try:
            # a table of correlations between every pair of columns
            corr = df[corr_cols].apply(pd.to_numeric, errors='coerce').corr()
            # look at each pair once
            for i, a in enumerate(corr_cols):
                for b in corr_cols[i + 1:]:
                    # the correlation (r) between a and b
                    r = corr.loc[a, b]
                    # keep only moderate-to-strong relationships (|r| of 0.4 or more)
                    if pd.notna(r) and abs(r) >= 0.4:
                        pairs.append((abs(r), float(r), a, b))
        # if anything goes wrong, just skip scatter recommendations
        except Exception:
            pairs = []
        # strongest relationships first
        pairs.sort(reverse=True)

    # recommend a scatter plot for the top 2 pairs
    for _abs_r, r, a, b in pairs[:2]:
        add('scatter', f"{b} vs {a}",
            f"Checks whether {a} and {b} move together: {describe_correlation(r)} relationship (r = {r:.2f}).",
            {'x_column': a, 'y_column': b})

    # categories with 6 or fewer groups, good for coloring dots
    color_groups = [c for c in group_cols if meta(c)['unique'] <= 6]
    # if there is a strong pair AND a color category, recommend a colored scatter
    if pairs and color_groups:
        _abs_r, r, a, b = pairs[0]
        c = color_groups[0]
        add('colored_scatter', f"{b} vs {a}, colored by {c}",
            f"The same relationship with points colored by {c}, to see whether the groups behave differently.",
            {'x_column': a, 'y_column': b, 'color_column': c})

    # --- Distribution: how a single numeric column is spread ---
    for m in analysis_cols[:2]:
        add('distribution', f"Distribution of {m}",
            f"Shows how {m} values are spread: the typical range, skew and any outliers.",
            {'y_column': m})

    # --- Scatter matrix: screen several numeric columns at once ---
    if len(analysis_cols) >= 3:
        # use the first 4 columns
        cols = analysis_cols[:4]
        add('scatter_matrix', "Scatter matrix: " + ", ".join(cols),
            "Compares every pair of these variables at once to spot which relationships deserve a closer look.",
            {'matrix_columns': cols})

    # return the finished list
    return recs


# Converts a matplotlib figure into text (base64) so it can be placed inside an HTML <img> tag.
def fig_to_base64(fig):
    # an in-memory file to save the picture into
    buf = io.BytesIO()
    # save the figure as a PNG image into that memory file
    fig.savefig(buf, format='png', bbox_inches='tight')
    # go back to the start of the memory file
    buf.seek(0)
    # turn the image bytes into base64 text and return it
    return base64.b64encode(buf.read()).decode('utf-8')


# Makes a short title and subtitle for a chart (shown on the Analysis page and in the Report Builder).
def describe_chart(chart_type, x_column, y_column, color_column, agg='sum'):
    """Short human title used on the Analysis page and the Report Builder list."""
    # friendly names for each chart type
    type_labels = {
        'bar': 'Bar Chart', 'line': 'Line Chart', 'pie': 'Pie Chart',
        'scatter': 'Scatter Plot', 'colored_scatter': 'Colored Scatter',
        'distribution': 'Distribution', 'scatter_matrix': 'Scatter Matrix',
    }
    # get the friendly name (or a tidy version of the raw value)
    label = type_labels.get(chart_type, chart_type.title())
    # show "Count" instead of the internal value "__count__"
    y_label = 'Count' if y_column == '__count__' else y_column
    # for bar, line, and pie charts, add the calculation word (Average, Median...) when it is not the usual Sum
    if chart_type in ('bar', 'line', 'pie') and y_column != '__count__' and agg != 'sum':
        y_label = f"{AGG_LABELS[agg]} {y_column}"

    # a scatter matrix uses many columns, so use a general subtitle
    if chart_type == 'scatter_matrix':
        return f"{label}", "Multiple numeric variables"
    # a distribution only uses the Y column
    if chart_type == 'distribution':
        return f"{label}: {y_column}", f"Distribution of {y_column}"
    # a colored scatter mentions the color category
    if chart_type == 'colored_scatter':
        return f"{label}: {y_column} vs {x_column}", f"Colored by {color_column}"
    # all other charts
    return f"{label}: {y_label} by {x_column}", f"{x_column} vs {y_label}"


# ------------------------------------------------------------
# WRITTEN INSIGHTS
# ------------------------------------------------------------

# Turns a correlation number (r) into words like "a strong positive".
def describe_correlation(corr):
    # if the value is missing, we can't describe it
    if pd.isna(corr):
        return "an undetermined"
    # the strength ignores the sign (+ or -)
    abs_corr = abs(corr)
    # 0.7 and above is strong
    if abs_corr >= 0.7:
        strength = "a strong"
    # 0.4 to 0.7 is moderate
    elif abs_corr >= 0.4:
        strength = "a moderate"
    # 0.2 to 0.4 is weak
    elif abs_corr >= 0.2:
        strength = "a weak"
    # below 0.2 is basically nothing
    else:
        strength = "little to no"
    # positive means both go up together, negative means one goes up as the other goes down
    direction = "positive" if corr >= 0 else "negative"
    # include the direction only if there is a real relationship
    return f"{strength} {direction}" if abs_corr >= 0.2 else strength


# Writes the automatic "Key Findings", "Risks & Opportunities" and "Recommendations" for a chart.
def generate_insights(df, chart_type, x_column, y_column, color_column, agg='sum'):
    """Returns a dict with three short lists of plain-language lines:
    'key_findings', 'risks_opportunities', 'strategic_recommendations'."""
    # three empty lists to fill with sentences
    key_findings, risks, recommendations = [], [], []

    # 'try' so that an unexpected problem never crashes the page
    try:
        # ---- bar / line / pie: compare groups ----
        if chart_type in ('bar', 'line', 'pie'):
            # if counting records...
            if y_column == '__count__':
                # count rows per group, biggest first
                grouped = df.groupby(x_column).size().sort_values(ascending=False)
                # the word used in the sentences
                label = "records"
                # counting records is always a total
                agg_word = 'total'
            # otherwise group the Y column and calculate it (sum, average, median, min, or max)
            else:
                numeric_y = pd.to_numeric(df[y_column], errors='coerce')
                grouped = numeric_y.groupby(df[x_column]).agg(agg).dropna().sort_values(ascending=False)
                label = y_column
                # the word used in the sentences: total, average, median, minimum, or maximum
                agg_word = AGG_WORDS[agg]
            # True when the numbers can be added together (a total), so "share of the total" makes sense
            is_total = agg_word == 'total'

            # only write insights if there are groups
            if len(grouped):
                # the total of all groups
                total_val = grouped.sum()
                # the average group value
                avg_val = grouped.mean()
                # how many groups there are
                n_categories = len(grouped)
                # the top group's name and value (the list is sorted, so it is first)
                top_cat, top_val = grouped.index[0], grouped.iloc[0]
                # how far (in %) the top group is above the average
                pct_above_avg = ((top_val - avg_val) / avg_val * 100) if avg_val else 0
                # the top group's share (in %) of the total
                top_share = (top_val / total_val * 100) if total_val else 0

                # the first finding: who is highest
                key_findings.append(
                    f"'{top_cat}' has the highest {agg_word} {label} at {top_val:.2f}"
                    + (f", about {pct_above_avg:.1f}% above the group average of {avg_val:.2f}." if avg_val else ".")
                )

                # more findings only make sense with 2 or more groups
                if n_categories > 1:
                    # the lowest group's name and value (last in the sorted list)
                    low_cat, low_val = grouped.index[-1], grouped.iloc[-1]
                    # finding: who is lowest
                    key_findings.append(f"'{low_cat}' has the lowest at {low_val:.2f}.")
                    # finding: for totals, the overall total and the top group's share
                    if is_total:
                        key_findings.append(
                            f"Across {n_categories} {x_column.lower()}s, total {label} reaches {total_val:.2f}, "
                            f"with '{top_cat}' making up {top_share:.1f}% of that total."
                        )
                    # for averages, medians, minimums and maximums, adding the groups together would be meaningless,
                    # so show the range from the lowest group to the highest group instead
                    else:
                        key_findings.append(
                            f"Across {n_categories} {x_column.lower()}s, the {agg_word} {label} ranges "
                            f"from {low_val:.2f} to {top_val:.2f}."
                        )

                    # the gap between highest and lowest
                    spread = top_val - low_val
                    # the middle value of all groups
                    median_val = grouped.median()
                    # how many groups are below average
                    below_avg_count = int((grouped < avg_val).sum())

                    # risk: the size of the gap
                    risks.append(f"The gap between the highest and lowest {x_column} is {spread:.2f}.")
                    # risk: how many groups are under the average
                    risks.append(
                        f"{below_avg_count} of {n_categories} {x_column.lower()}s fall below the group average "
                        f"({avg_val:.2f})."
                    )
                    # opportunity: improving the weaker groups
                    risks.append(
                        f"Bringing the lower-performing {x_column.lower()}s closer to the median "
                        f"({median_val:.2f}) would {'lift the overall total' if is_total else 'improve the overall result'}."
                    )

                    # with 3+ groups, mention the runner-up
                    if n_categories >= 3:
                        # the second-highest group
                        runner_up_cat, runner_up_val = grouped.index[1], grouped.iloc[1]
                        # how far it is behind the top group
                        gap_to_runner_up = top_val - runner_up_val
                        # recommendation: use the runner-up as a realistic target
                        recommendations.append(
                            f"'{runner_up_cat}' is closest to '{top_cat}' (behind by {gap_to_runner_up:.2f}) "
                            f"— worth a look if you want a realistic target to catch up to the top spot."
                        )

                    # recommendation: investigate the lowest group
                    recommendations.append(f"Look into why '{low_cat}' is behind '{top_cat}'.")
                # if there is only one group...
                else:
                    # replace the first finding with a simpler sentence
                    key_findings[
                        -1] = f"The only {x_column} shown, '{top_cat}', has a {agg_word} {label} of {top_val:.2f}."

                # recommendation: keep tracking
                recommendations.append(
                    f"Keep tracking {label} over time against the current top value ({top_val:.2f}).")

        # ---- scatter / colored scatter: relationship between two numbers ----
        elif chart_type in ('scatter', 'colored_scatter'):
            # both columns as numbers
            x_num = pd.to_numeric(df[x_column], errors='coerce')
            y_num = pd.to_numeric(df[y_column], errors='coerce')
            # the correlation (r) between them: -1 to +1
            corr = x_num.corr(y_num)
            # use 0 if the correlation could not be calculated
            corr_val = corr if pd.notna(corr) else 0

            # finding: the strength of the relationship
            key_findings.append(
                f"{x_column} and {y_column} show {describe_correlation(corr)} relationship (r = {corr_val:.2f})."
            )
            # for a colored scatter, mention the grouping
            if chart_type == 'colored_scatter' and color_column:
                key_findings.append(f"Points are grouped by {color_column} to compare subgroups.")

            # if the relationship is moderate or stronger...
            if pd.notna(corr) and abs(corr) >= 0.4:
                risks.append(
                    f"Since {y_column} closely follows {x_column}, a change in one could noticeably affect the other.")
                recommendations.append(f"Consider how changes in {x_column} might affect {y_column} going forward.")
            # if the relationship is weak...
            else:
                risks.append(
                    f"{x_column} alone doesn't explain much of the change in {y_column} — other factors are likely involved.")
                recommendations.append(
                    f"Look at other variables that might explain {y_column} better than {x_column} does.")

        # ---- distribution: how one column is spread ----
        elif chart_type == 'distribution':
            # the column as numbers, without blanks
            values = pd.to_numeric(df[y_column], errors='coerce').dropna()
            # only continue if there are values
            if len(values):
                # average, middle value, and standard deviation (how spread out values are)
                mean_v, median_v, std_v = values.mean(), values.median(), values.std()
                # finding: average and median
                key_findings.append(f"{y_column} averages {mean_v:.2f} (median {median_v:.2f}).")

                # skew tells whether the values lean to one side
                skew = values.skew()
                if pd.notna(skew):
                    # positive skew: a few very high values
                    if skew > 0.5:
                        key_findings.append(
                            "Most values sit below the average, with a few high outliers pulling it up.")
                        recommendations.append(f"Double-check the highest {y_column} values — are they accurate?")
                    # negative skew: a few very low values
                    elif skew < -0.5:
                        key_findings.append(
                            "Most values sit above the average, with a few low outliers pulling it down.")
                        recommendations.append(f"Double-check the lowest {y_column} values — are they accurate?")
                    # roughly balanced
                    else:
                        key_findings.append("Values are spread fairly evenly around the average.")

                # if the average is not zero...
                if mean_v:
                    # coefficient of variation: spread compared to the average, as a %
                    cv = (std_v / mean_v) * 100
                    # 40% or more means the values vary a lot
                    if cv >= 40:
                        risks.append(
                            f"{y_column} varies quite a bit from one record to the next, which makes it harder to predict.")

        # ---- scatter matrix: general advice ----
        elif chart_type == 'scatter_matrix':
            key_findings.append(
                "This grid compares every pair of selected variables at once — look for diagonal patterns and clusters.")
            risks.append(
                "Variables that move tightly together can make it harder to tell which one is actually driving the result.")
            recommendations.append(
                "Focus on the one or two pairs with the clearest pattern rather than all of them equally.")

    # if anything above failed, use a safe fallback message
    except Exception:
        key_findings.append("Not enough data was available to generate insights for this chart configuration.")

    # make sure each list has at least one sentence
    if not key_findings:
        key_findings.append("Select variables and generate a chart to see insights here.")
    if not risks:
        risks.append("No notable risks or opportunities detected for this configuration.")
    if not recommendations:
        recommendations.append("Add more data or adjust variables to receive tailored recommendations.")

    # return the three lists in one dictionary
    return {
        'key_findings': key_findings,
        'risks_opportunities': risks,
        'strategic_recommendations': recommendations,
    }


# ------------------------------------------------------------
# SHARED PARAM RESOLUTION
# ------------------------------------------------------------

# Reads the chart choices from the URL (e.g. ?chart_type=bar&x_column=Status) and fills in defaults.
def get_view_params(request, categorical_columns, numeric_columns, all_columns):
    # the chart type; default is a bar chart
    chart_type = request.GET.get('chart_type', 'bar')
    # the X column; default is the first categorical column, or else the first column
    x_column = request.GET.get('x_column') or (
        categorical_columns[0] if categorical_columns else (all_columns[0] if all_columns else None)
    )
    # the Y column; default is the first numeric column
    y_column = request.GET.get('y_column') or (numeric_columns[0] if numeric_columns else None)
    # the color column; default is the first categorical column
    color_column = request.GET.get('color_column') or (categorical_columns[0] if categorical_columns else None)
    # the list of columns ticked for a scatter matrix
    matrix_columns = request.GET.getlist('matrix_columns')
    # return all five choices
    return chart_type, x_column, y_column, color_column, matrix_columns


# Shared setup used by both the Analysis page and the PDF export.
def _prepare_dataset_context(request):
    """Shared setup used by both the visualization page and the PDF export."""
    # get the user's newest dataset
    dataset = Dataset.objects.filter(user=request.user).order_by('-uploaded_at').first()
    # none uploaded yet
    if not dataset:
        return None

    # load it as a table
    df = get_dataset_dataframe(dataset)
    # if the table is empty, return early
    if df.empty:
        return {'dataset': dataset, 'df': df}

    # add financial ratio columns (does nothing for non-financial data)
    df, ratio_columns, is_financial, fin_mapping = add_financial_ratios(df)
    # find the numeric and categorical columns
    numeric_columns, categorical_columns = detect_column_types(df)
    # all the column names
    all_columns = list(df.columns)
    # info cards about each column
    column_meta = build_column_meta(df, numeric_columns, categorical_columns)

    # read the user's chart choices from the URL
    chart_type, x_column, y_column, color_column, matrix_columns = get_view_params(
        request, categorical_columns, numeric_columns, all_columns
    )
    # the calculation picked for each group (sum, mean, median, min, or max); 'sum' if nothing valid was sent
    agg = _clean_agg(request.GET.get('agg'))
    date_interval = 'original'  # Line charts use the original date values; no Group Date By option.

    # bundle everything into one dictionary
    return {
        'dataset': dataset,
        'df': df,
        'ratio_columns': ratio_columns,
        'is_financial': is_financial,
        'fin_mapping': fin_mapping,
        'numeric_columns': numeric_columns,
        'categorical_columns': categorical_columns,
        'all_columns': all_columns,
        'column_meta': column_meta,
        'chart_type': chart_type,
        'x_column': x_column,
        'y_column': y_column,
        'color_column': color_column,
        'matrix_columns': matrix_columns,
        'agg': agg,
        'date_interval': date_interval,
    }


# Packs the chart choices into one small dictionary (this is what we store in the report queue).
def _config_from_params(chart_type, x_column, y_column, color_column, matrix_columns, agg='sum', date_interval='original'):
    return {
        'chart_type': chart_type,
        'x_column': x_column,
        'y_column': y_column,
        'color_column': color_column,
        # the calculation used for each group: sum, mean, median, min, or max
        'agg': agg,
        'date_interval': _clean_date_interval(date_interval),
        # make a real list (and an empty one if nothing was given)
        'matrix_columns': list(matrix_columns or []),
    }


# ------------------------------------------------------------
# VISUALIZATION VIEW
# ------------------------------------------------------------

# The view for the Analysis page (the main charting page).
@login_required
def visualization(request):
    # run the shared setup
    ctx = _prepare_dataset_context(request)
    # if there is no dataset, show an error
    if ctx is None:
        return render(request, 'dashboard/visualization.html', {'error': 'No dataset has been uploaded yet.'})

    # take out the dataset and its table
    dataset, df = ctx['dataset'], ctx['df']
    # if the table is empty, show an error
    if df.empty:
        return render(request, 'dashboard/visualization.html', {'dataset': dataset, 'error': 'No usable rows.'})

    # Pivot tables are generated on demand and rendered only on this page. They are never placed in report_queue.
    pivot_html, pivot_error = None, None
    pivot_rows = request.GET.get('pivot_rows') or (ctx['categorical_columns'][0] if ctx['categorical_columns'] else ctx['all_columns'][0])
    pivot_columns = request.GET.get('pivot_columns', '')
    pivot_values = request.GET.get('pivot_values', '__records__')
    pivot_agg = request.GET.get('pivot_agg', 'total')
    allowed_pivot_aggs = {'sum', 'count', 'mean', 'max', 'min', 'std', 'nunique', 'total'}
    if request.GET.get('show_pivot') == '1':
        try:
            if pivot_rows not in df.columns:
                raise ValueError('Please choose a valid Rows column.')
            if pivot_columns and pivot_columns not in df.columns:
                raise ValueError('Please choose a valid Columns field.')
            if pivot_values != '__records__' and pivot_values not in df.columns:
                raise ValueError('Please choose a valid Values field.')
            if pivot_agg not in allowed_pivot_aggs:
                pivot_agg = 'total'

            if pivot_values == '__records__' or pivot_agg == 'total':
                # Total means number of source records in each group.
                if pivot_columns:
                    pivot_df = pd.crosstab(df[pivot_rows], df[pivot_columns], margins=True, margins_name='Total')
                else:
                    pivot_df = df.groupby(pivot_rows, dropna=False).size().to_frame('Total Records')
                    pivot_df.loc['Total'] = pivot_df.sum(numeric_only=True)
            else:
                aggfunc = {'sum': 'sum', 'count': 'count', 'mean': 'mean', 'max': 'max', 'min': 'min', 'std': 'std', 'nunique': lambda values: values.nunique(dropna=True)}[pivot_agg]
                if pivot_columns:
                    pivot_df = pd.pivot_table(df, index=pivot_rows, columns=pivot_columns, values=pivot_values, aggfunc=aggfunc, dropna=False, margins=True, margins_name='Total')
                else:
                    grouped = df.groupby(pivot_rows, dropna=False)[pivot_values]
                    if pivot_agg == 'nunique':
                        result = grouped.nunique(dropna=True)
                    else:
                        result = getattr(grouped, {'mean': 'mean', 'std': 'std', 'sum': 'sum', 'count': 'count', 'max': 'max', 'min': 'min'}[pivot_agg])()
                    pivot_df = result.to_frame(pivot_values)
                    pivot_df.loc['Total'] = [df[pivot_values].nunique(dropna=True) if pivot_agg == 'nunique' else getattr(df[pivot_values], {'mean':'mean','std':'std','sum':'sum','count':'count','max':'max','min':'min'}[pivot_agg])()]
            pivot_html = pivot_df.to_html(classes='data-table', border=0, na_rep='—', escape=True)
        except Exception as exc:
            pivot_error = str(exc)

    # start with no chart, no error, empty insights, and empty titles
    chart_image, error = None, None
    insights = {'key_findings': [], 'risks_opportunities': [], 'strategic_recommendations': []}
    chart_title, chart_subtitle = '', ''

    # try to draw the chart
    try:
        # build the chart figure
        fig = build_chart(
            df, ctx['chart_type'], ctx['x_column'], ctx['y_column'],
            ctx['color_column'], ctx['matrix_columns'], ctx['numeric_columns'], ctx['agg'], ctx['date_interval'],
        )
        # convert the figure to text so the HTML page can show it
        chart_image = fig_to_base64(fig)
        # close the figure to free up memory
        plt.close(fig)
        # write the automatic insights
        insights = generate_insights(
            df, ctx['chart_type'], ctx['x_column'], ctx['y_column'], ctx['color_column'], ctx['agg']
        )
        # make the chart's title and subtitle
        chart_title, chart_subtitle = describe_chart(
            ctx['chart_type'], ctx['x_column'], ctx['y_column'], ctx['color_column'], ctx['agg']
        )
    # if drawing failed, keep the error text to show on the page
    except Exception as e:
        error = str(e)

    # ---- EDIT MODE: reopening a chart that is already in the report ----
    # the Edit (pencil) button in the Report Builder opens this page with ?edit=<position in the report>
    edit_index = None
    # read the position as a whole number (a missing or bad value means "not editing")
    try:
        candidate = int(request.GET.get('edit', ''))
    except ValueError:
        candidate = None
    # the charts currently queued in the report
    queue_now = request.session.get(REPORT_SESSION_KEY, [])
    # only accept a position that really exists in the queue
    if candidate is not None and 0 <= candidate < len(queue_now):
        edit_index = candidate
        saved_item = queue_now[candidate]
        # if the chart on the screen still has the same settings as the saved one, show the text the user
        # already edited; if they changed the settings, keep the freshly written insights for the new chart
        if chart_image and _same_chart_settings(saved_item.get('config', {}), ctx):
            insights = {
                'key_findings': _text_to_lines(saved_item.get('key_findings_text', '')),
                'risks_opportunities': _text_to_lines(saved_item.get('risks_text', '')),
                'strategic_recommendations': _text_to_lines(saved_item.get('recommendations_text', '')),
            }

    # the financial info (only used when the data looks like a financial statement)
    is_financial = ctx['is_financial']
    ratio_columns = ctx['ratio_columns']
    # sentences describing the latest ratios
    financial_notes = financial_ratio_narrative(df, ratio_columns) if is_financial else []
    # an HTML table of the last 12 rows of ratios
    ratio_table = None
    if is_financial:
        ratio_table = df[ratio_columns].round(2).tail(12).to_html(classes='data-table', border=0, na_rep='—')

    # the charts already queued for the report (used for the navbar badge)
    report_queue = request.session.get(REPORT_SESSION_KEY, [])

    # try to build the "Recommended Charts" list
    try:
        chart_recommendations = build_chart_recommendations(
            df, ctx['numeric_columns'], ctx['categorical_columns'], ctx['column_meta'], ratio_columns,
        )
    # if it fails, use an empty list instead of crashing the page
    except Exception:
        chart_recommendations = []  # the guide is a nice-to-have; never break the page over it

    # everything the HTML template needs, packed in one dictionary
    context = {
        'dataset': dataset,
        'row_count': len(df),
        'column_count': len(df.columns),
        'columns': ctx['all_columns'],
        'numeric_columns': ctx['numeric_columns'],
        'categorical_columns': ctx['categorical_columns'],
        'chart_type': ctx['chart_type'],
        'x_column': ctx['x_column'],
        'y_column': ctx['y_column'],
        'color_column': ctx['color_column'],
        'chart_image': chart_image,
        'chart_title': chart_title,
        'chart_subtitle': chart_subtitle,
        'error': error,
        'selected_matrix': ctx['matrix_columns'],
        'key_findings': insights['key_findings'],
        'risks_opportunities': insights['risks_opportunities'],
        'strategic_recommendations': insights['strategic_recommendations'],
        'is_financial': is_financial,
        'ratio_columns': ratio_columns,
        'financial_notes': financial_notes,
        'ratio_table': ratio_table,
        # the current URL's query text, so "Add to Report" can come back to the same chart
        'query_string': request.GET.urlencode(),
        'report_count': len(report_queue),
        # --- Data Preview toggle ---
        'data_preview': build_data_preview(df),
        'data_preview_max_rows': DATA_PREVIEW_MAX_ROWS,
        # --- Concept Guide modal (per chart type) ---
        'concept_guides_json': json.dumps(CONCEPT_GUIDES),
        # --- Dynamic chart-type recommendation note ---
        'column_meta_json': json.dumps(ctx['column_meta']),
        # --- Recommended Charts guide ---
        'chart_recommendations': chart_recommendations,
        # --- the calculation picked for each group (sum, mean, median, min, max) ---
        'agg': ctx['agg'],
        'date_interval': ctx['date_interval'],
        # --- edit mode: the position of the chart being edited (None when adding a new chart) ---
        'edit_index': edit_index,
        # the same position counted from 1, for the message shown to the user
        'edit_number': (edit_index + 1) if edit_index is not None else None,
        # Pivot table settings/results are page-only and are deliberately excluded from report_queue.
        'pivot_html': pivot_html,
        'pivot_error': pivot_error,
        'pivot_rows': pivot_rows,
        'pivot_columns': pivot_columns,
        'pivot_values': pivot_values,
        'pivot_agg': pivot_agg,
    }
    # show the Analysis page
    return render(request, 'dashboard/visualization.html', context)


# ------------------------------------------------------------
# REPORT QUEUE (session-backed: holds chart configs + edited insights
# selected from the Analysis page, consumed by the Report Builder page)
# ------------------------------------------------------------

# The view that runs when the user clicks "+ Add to Report".
@login_required
def add_to_report(request):
    """POST target for the 'Add to Report' button on the Analysis page.
    Reads the currently-selected chart config plus the (possibly edited)
    insights text and appends it to the session-backed queue."""
    # only form submissions are allowed
    if request.method != 'POST':
        return redirect('visualization')

    # read the chart choices sent by the hidden form fields
    chart_type = request.POST.get('chart_type', 'bar')
    x_column = request.POST.get('x_column', '')
    y_column = request.POST.get('y_column', '')
    color_column = request.POST.get('color_column', '')
    matrix_columns = request.POST.getlist('matrix_columns')
    # the calculation used for each group (sum, mean, median, min, or max)
    agg = _clean_agg(request.POST.get('agg'))
    date_interval = _clean_date_interval(request.POST.get('date_interval'))
    # read the three text boxes (the user may have edited them)
    key_findings_text = request.POST.get('key_findings_text', '')
    risks_text = request.POST.get('risks_text', '')
    recommendations_text = request.POST.get('recommendations_text', '')

    # make the chart's title and subtitle
    title, subtitle = describe_chart(chart_type, x_column, y_column, color_column, agg)

    # everything we remember about this chart
    new_item = {
        'config': _config_from_params(chart_type, x_column, y_column, color_column, matrix_columns, agg, date_interval),
        'title': title,
        'subtitle': subtitle,
        'key_findings_text': key_findings_text,
        'risks_text': risks_text,
        'recommendations_text': recommendations_text,
    }

    # get the current queue (or an empty list)
    queue = request.session.get(REPORT_SESSION_KEY, [])
    # when the user is editing a chart that is already in the report, the form also sends its position
    try:
        edit_idx = int(request.POST.get('edit_index', ''))
    except ValueError:
        edit_idx = None
    # True only if that position really exists in the queue
    editing = edit_idx is not None and 0 <= edit_idx < len(queue)
    if editing:
        # replace the old chart with the updated one (it keeps its place in the report)
        queue[edit_idx] = new_item
    else:
        # otherwise add this chart to the end of the queue
        queue.append(new_item)
    # save the queue back into the session
    request.session[REPORT_SESSION_KEY] = queue
    # tell Django the session changed, so it gets saved
    request.session.modified = True

    # after updating a chart, go back to the Report Builder to see it in the list
    if editing:
        return redirect('report_builder')

    # go back to the same chart the user was looking at
    query_string = request.POST.get('query_string', '')
    redirect_url = '/visualization/'
    if query_string:
        redirect_url += f'?{query_string}'
    return redirect(redirect_url)


# The view that runs when the user clicks the Edit (pencil) button on a chart in the Report Builder.
# It sends the user back to the Analysis page with that chart's settings filled in.
@login_required
def edit_report_item(request, index):
    # get the queue
    queue = request.session.get(REPORT_SESSION_KEY, [])
    # if that position does not exist, just go back to the Report Builder
    if not (0 <= index < len(queue)):
        return redirect('report_builder')
    # the chart settings that were saved when the chart was added
    config = queue[index].get('config', {})
    # turn them into the same web address options the Analysis page normally uses
    params = {
        'chart_type': config.get('chart_type', 'bar'),
        'x_column': config.get('x_column', ''),
        'y_column': config.get('y_column', ''),
        'color_column': config.get('color_column', ''),
        'agg': _clean_agg(config.get('agg')),
        'matrix_columns': config.get('matrix_columns', []),
        # "edit" tells the Analysis page which chart in the report is being edited
        'edit': index,
    }
    # open the Analysis page (doseq=True lets the list of matrix columns become several options)
    return redirect(f"{reverse('visualization')}?{urlencode(params, doseq=True)}")


# The view that removes one chart from the report queue.
@login_required
def remove_from_report(request, index):
    # get the queue
    queue = request.session.get(REPORT_SESSION_KEY, [])
    # only act if the index is a valid position
    if 0 <= index < len(queue):
        # remove that chart
        queue.pop(index)
        # save the changed queue
        request.session[REPORT_SESSION_KEY] = queue
        request.session.modified = True
    # go back to the Report Builder
    return redirect('report_builder')


# The view that moves a chart up or down in the report queue.
@login_required
def reorder_report(request, index, direction):
    # get the queue
    queue = request.session.get(REPORT_SESSION_KEY, [])
    # the position to swap with: one earlier for "up", one later for "down"
    target = index - 1 if direction == 'up' else index + 1
    # only swap if both positions exist
    if 0 <= index < len(queue) and 0 <= target < len(queue):
        # swap the two charts
        queue[index], queue[target] = queue[target], queue[index]
        # save the changed queue
        request.session[REPORT_SESSION_KEY] = queue
        request.session.modified = True
    # go back to the Report Builder
    return redirect('report_builder')


# ------------------------------------------------------------
# REPORT PARAMETERS (drafted from the charts queued for the report)
# ------------------------------------------------------------

# what kind of analysis each chart type represents (used in the report subtitle)
CHART_PURPOSES = {
    'bar': 'Comparison', 'pie': 'Comparison', 'line': 'Trend',
    'scatter': 'Relationship', 'colored_scatter': 'Relationship', 'scatter_matrix': 'Relationship',
    'distribution': 'Distribution',
}


# Joins words into a natural list: ["a", "b", "c"] -> "a, b and c".
def _join_words(items):
    # convert to text and drop empty items
    items = [str(i) for i in items if i]
    # with 0 or 1 item, there is nothing to join
    if len(items) <= 1:
        return ''.join(items)
    # commas between all but the last, then "and" before the last
    return ', '.join(items[:-1]) + ' and ' + items[-1]


# Makes a file name look nice: "Student_Performance.xlsx" -> "Student Performance".
def _friendly_dataset_name(filename):
    # remove the file extension (the part after the last dot)
    base = filename.rsplit('.', 1)[0] if '.' in filename else filename
    # replace underscores and dashes with spaces; fall back to "the dataset" if nothing is left
    return base.replace('_', ' ').replace('-', ' ').strip() or 'the dataset'


# Drafts a title, subtitle, and executive overview from the charts queued for the report.
def build_report_suggestions(queue, dataset_name, record_count):
    """Title, subtitle and executive overview drafted from the queued charts."""
    # with no charts, there is nothing to suggest
    if not queue:
        return {'title': '', 'subtitle': '', 'overview': ''}

    # lists that collect the metrics, groups, purposes, and sentence pieces from all charts
    metrics, groups, purposes, clauses = [], [], [], []

    # inner helper: add a value to a list only if it is not empty and not already there
    def remember(bucket, value):
        if value and value not in bucket:
            bucket.append(value)

    # look at each queued chart
    for item in queue:
        # the chart's saved choices
        cfg = item.get('config', {})
        ctype = cfg.get('chart_type')
        x, y, color = cfg.get('x_column'), cfg.get('y_column'), cfg.get('color_column')
        # was the chart counting records?
        is_count = (y == '__count__')
        # the metric's display name
        y_name = 'Record Count' if is_count else y
        # the word for the calculation saved with this chart (total, average, median...)
        agg_word = AGG_WORDS[_clean_agg(cfg.get('agg'))]

        # remember what kind of analysis this chart is
        remember(purposes, CHART_PURPOSES.get(ctype))

        # for a bar chart...
        if ctype == 'bar':
            remember(metrics, y_name)
            remember(groups, x)
            clauses.append(f"counts records by {x}" if is_count else f"compares {agg_word} {y} across {x}")
        # for a pie chart...
        elif ctype == 'pie':
            remember(metrics, y_name)
            remember(groups, x)
            clauses.append(
                f"shows each {x}'s share of all records" if is_count
                else f"shows each {x}'s share of {agg_word} {y}"
            )
        # for a line chart...
        elif ctype == 'line':
            remember(metrics, y_name)
            remember(groups, x)
            clauses.append(f"tracks record count across {x}" if is_count else f"tracks {agg_word} {y} across {x}")
        # for scatter and colored scatter...
        elif ctype in ('scatter', 'colored_scatter'):
            remember(metrics, x)
            remember(metrics, y)
            clause = f"examines the relationship between {x} and {y}"
            # if colored, mention the color category
            if ctype == 'colored_scatter' and color:
                remember(groups, color)
                clause += f", colored by {color}"
            clauses.append(clause)
        # for a distribution...
        elif ctype == 'distribution':
            remember(metrics, y_name)
            clauses.append(f"looks at how {y} is distributed")
        # for a scatter matrix...
        elif ctype == 'scatter_matrix':
            cols = cfg.get('matrix_columns') or []
            for col in cols:
                remember(metrics, col)
            clauses.append(
                f"compares {_join_words(cols[:4])} against each other" if cols
                else "compares several numeric variables against each other"
            )

    # a nice-looking dataset name
    source = _friendly_dataset_name(dataset_name or '')

    # Title: named after the metric(s) when the report is focused, otherwise after the dataset.
    if len(metrics) == 1:
        title = f"{metrics[0]} Analysis" + (f" by {groups[0]}" if len(groups) == 1 else "")
    elif len(metrics) == 2:
        title = f"{metrics[0]} and {metrics[1]} Analysis"
    else:
        title = f"{source} Analysis Report"

    # Subtitle: what kind of analysis the charts add up to.
    subtitle = f"{_join_words(purposes)} Analysis"
    if 1 <= len(groups) <= 2:
        subtitle += f" by {_join_words(groups)}"

    # Executive overview: one sentence per chart (capped), plus what the report contains.
    # number of charts
    n = len(queue)
    # the record count text, e.g. " (42 records)"
    records = f" ({record_count:,} records)" if record_count else ""
    # if there are more than 5 charts, only describe 5 and summarize the rest
    if len(clauses) > 5:
        extra = len(clauses) - 5
        clauses = clauses[:5] + [f"covers {extra} more chart{'' if extra == 1 else 's'}"]

    # build the overview sentence by sentence
    parts = [f"This report presents {n} chart{'' if n == 1 else 's'} built from {source}{records}."]
    if clauses:
        parts.append("It " + _join_words(clauses) + ".")
    parts.append("Each chart is followed by its key findings, risks and opportunities, and strategic recommendations.")

    # return the three drafted texts
    return {'title': title, 'subtitle': subtitle, 'overview': " ".join(parts)}


# The view for the Report Builder page.
@login_required
def report_builder(request):
    # import date here (only needed in this function)
    from datetime import date

    # the user's newest dataset
    dataset = Dataset.objects.filter(user=request.user).order_by('-uploaded_at').first()
    # the charts queued for the report
    queue = request.session.get(REPORT_SESSION_KEY, [])

    # how many rows the dataset has (0 if there is no dataset)
    row_count = DataRow.objects.filter(dataset=dataset).count() if dataset else 0
    # draft the title, subtitle, and overview
    suggestions = build_report_suggestions(queue, dataset.name if dataset else '', row_count)

    # everything the template needs
    context = {
        'dataset': dataset,
        'row_count': row_count,
        'report_count': len(queue),
        # one entry per queued chart, with its position number
        'sections': [
            {'index': i, 'title': item['title'], 'subtitle': item['subtitle']}
            for i, item in enumerate(queue)
        ],
        # today's date written like "October 14, 2026"
        'today': date.today().strftime('%B %d, %Y'),
        # "Prepared By" is no longer a free-text field - it's always the
        # logged-in account's name, shown read-only on the Report page.
        'prepared_by': request.user.get_full_name() or request.user.get_username(),
        # Drafted from the charts in the report; the user can still edit them.
        'suggested_title': suggestions['title'],
        'suggested_subtitle': suggestions['subtitle'],
        'suggested_overview': suggestions['overview'],
    }
    # show the page
    return render(request, 'dashboard/report.html', context)


# ------------------------------------------------------------
# PDF REPORT DESIGN TOKENS
# ------------------------------------------------------------

# the colors used in the PDF (hex color codes)
PDF_ACCENT = '#1f5c3f'  # deep green — headings, rules, brand mark
PDF_MUTED = '#6b7280'
PDF_RULE_LIGHT = '#d1d5db'
PDF_BODY = '#374151'
PDF_HEADING = '#111827'


# ------------------------------------------------------------
# PDF PAGE CHROME (thin rule header/footer, drawn on every
# content page — cover page is handled separately)
# ------------------------------------------------------------

# Draws the header (top) and footer (bottom) on a PDF page.
# Positions like [0.09, 0.955, 0.82, 0.025] mean [left, bottom, width, height] as fractions of the page.
def _draw_chrome(fig, report_title, prepared_by='', page_number=None):
    # create a small drawing area for the header
    header_ax = fig.add_axes([0.09, 0.955, 0.82, 0.025])
    # hide its axis lines
    header_ax.axis('off')
    # use a simple 0-to-1 coordinate system
    header_ax.set_xlim(0, 1)
    header_ax.set_ylim(0, 1)
    # the brand name on the left of the header
    header_ax.text(0, 0.2, "QUICKSNAP", fontsize=10, fontweight='bold',
                   color=PDF_ACCENT, family='serif', va='bottom', ha='left')
    # the report title on the right of the header
    header_ax.text(1, 0.2, report_title, fontsize=9.5, color=PDF_MUTED,
                   family='serif', va='bottom', ha='right')

    # a thin drawing area for the green line under the header
    rule_ax = fig.add_axes([0.09, 0.95, 0.82, 0.0012])
    rule_ax.axis('off')
    rule_ax.set_xlim(0, 1)
    rule_ax.set_ylim(0, 1)
    # fill it with the green accent color to make the line
    rule_ax.add_patch(Rectangle((0, 0), 1, 1, color=PDF_ACCENT, transform=rule_ax.transAxes))

    # only draw the footer if a page number was given
    if page_number is not None:
        # a thin grey line above the footer
        frule_ax = fig.add_axes([0.09, 0.045, 0.82, 0.0012])
        frule_ax.axis('off')
        frule_ax.set_xlim(0, 1)
        frule_ax.set_ylim(0, 1)
        frule_ax.add_patch(Rectangle((0, 0), 1, 1, color=PDF_RULE_LIGHT, transform=frule_ax.transAxes))

        # the drawing area for the footer text
        footer_ax = fig.add_axes([0.09, 0.02, 0.82, 0.022])
        footer_ax.axis('off')
        footer_ax.set_xlim(0, 1)
        footer_ax.set_ylim(0, 1)
        # the left text: "Prepared by: name", or the brand name if there is no name
        left_label = f"Prepared by: {prepared_by}" if prepared_by else "QUICKSNAP"
        footer_ax.text(0, 0.5, left_label, fontsize=8.5, color=PDF_MUTED, family='serif', va='center', ha='left')
        # the right text: the page number
        footer_ax.text(1, 0.5, f"Page {page_number}", fontsize=8.5, color=PDF_MUTED, family='serif', va='center',
                       ha='right')


# ------------------------------------------------------------
# COVER PAGE
# ------------------------------------------------------------

# Draws the first page (cover) of the PDF report.
def _render_cover_page(pdf_pages, report_title, subtitle, prepared_by, report_date, organization, dataset_name):
    # create a blank page: 8.5 x 11 inches (letter size)
    fig = plt.figure(figsize=(8.5, 11))
    # one drawing area that covers the whole page
    ax = fig.add_axes([0, 0, 1, 1])
    # hide the axis lines
    ax.axis('off')
    # simple 0-to-1 coordinates (0,0 is bottom-left; 1,1 is top-right)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # the brand name at the top-left
    ax.text(0.09, 0.90, "QUICKSNAP", fontsize=11, fontweight='bold',
            color=PDF_ACCENT, family='serif', va='top', ha='left')
    # a green line under it
    ax.add_patch(Rectangle((0.09, 0.887), 0.32, 0.0018, color=PDF_ACCENT, transform=ax.transAxes))
    # the dataset name
    ax.text(0.09, 0.865, f"Dataset: {dataset_name}", fontsize=9.5, color=PDF_MUTED,
            family='serif', va='top', ha='left')

    # wrap the title so each line has at most 30 characters
    wrapped_title = "\n".join(textwrap.wrap(report_title, 30)) or report_title
    # count how many lines the title has
    title_lines = wrapped_title.count("\n") + 1
    # draw the big title in the middle of the page
    ax.text(0.5, 0.56, wrapped_title, fontsize=26, fontweight='bold', color=PDF_HEADING,
            family='serif', va='center', ha='center')

    # work out where the next text goes (lower down, depending on the title's height)
    next_y = 0.56 - 0.05 * title_lines - 0.03
    # draw the subtitle if there is one
    if subtitle:
        ax.text(0.5, next_y, subtitle, fontsize=12.5, color=PDF_MUTED, family='serif',
                va='center', ha='center', style='italic')
        next_y -= 0.05

    # the info lines (prepared by, date, organization) start at this height
    meta_y = min(next_y, 0.35)
    # draw each line that is not empty
    for line in filter(None, [
        f"Prepared by: {prepared_by}" if prepared_by else None,
        report_date or None,
        organization or None,
    ]):
        ax.text(0.5, meta_y, line, fontsize=10.5, color=PDF_BODY, family='serif', va='center', ha='center')
        # move down for the next line
        meta_y -= 0.028

    # a green line and brand name at the bottom of the cover
    ax.add_patch(Rectangle((0.09, 0.065), 0.32, 0.0018, color=PDF_ACCENT, transform=ax.transAxes))
    ax.text(0.09, 0.05, "QUICKSNAP", fontsize=9, fontweight='bold',
            color=PDF_ACCENT, family='serif', va='top', ha='left')

    # save this page into the PDF
    pdf_pages.savefig(fig)
    # close the figure to free memory
    plt.close(fig)


# ------------------------------------------------------------
# TABLE OF CONTENTS (dot-leader style)
# ------------------------------------------------------------

# Makes a line like "Chart Analysis ........ 4" (dots fill the space between the name and page number).
def _dot_leader_line(label, page_str, total_width=80):
    # how many dots fit (at least 3)
    dots_len = max(total_width - len(label) - len(page_str) - 1, 3)
    # build the line
    return f"{label} {'.' * dots_len} {page_str}"


# Draws the Table of Contents page.
def _render_toc_page(pdf_pages, report_title, prepared_by, overview_page_number, queue, first_chart_page):
    # a blank page with the header and footer
    fig = plt.figure(figsize=(8.5, 11))
    _draw_chrome(fig, report_title, prepared_by, overview_page_number - 1)

    # a full-page drawing area
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis('off')
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    # the page heading
    ax.text(0.09, 0.90, "Contents", fontsize=19, fontweight='bold', color=PDF_ACCENT, family='serif', va='top')

    # the first two entries
    entries = [("Report Overview", overview_page_number), ("Chart Analysis", first_chart_page)]
    # the chart pages start here
    page = first_chart_page
    # add one entry per chart, each on its own page
    for i, item in enumerate(queue, start=1):
        entries.append((f"    {i}. {item['title']}", page))
        page += 1

    # start drawing entries near the top
    y = 0.83
    for label, pg in entries:
        ax.text(0.09, y, _dot_leader_line(label, str(pg)), fontsize=10, color=PDF_HEADING,
                family='monospace', va='top')
        # move down for the next entry
        y -= 0.03

    # save the page and close it
    pdf_pages.savefig(fig)
    plt.close(fig)


# ------------------------------------------------------------
# REPORT OVERVIEW PAGE
# ------------------------------------------------------------

# Draws the Report Overview page (executive summary and dataset facts).
def _render_overview_page(pdf_pages, report_title, prepared_by, page_number, dataset_name,
                          row_count, column_count, numeric_count, categorical_count,
                          queue, executive_overview):
    # a blank page with header and footer
    fig = plt.figure(figsize=(8.5, 11))
    _draw_chrome(fig, report_title, prepared_by, page_number)

    # a full-page drawing area
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis('off')
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    # the page heading
    ax.text(0.09, 0.90, "Report Overview", fontsize=19, fontweight='bold', color=PDF_ACCENT, family='serif', va='top')

    # use the user's overview text, or a default sentence if it is empty
    intro = executive_overview or (
        "This report presents the charts generated in the QuickSnap dashboard, "
        "together with the key findings, risks, and recommendations behind each one."
    )
    # wrap the text so each line has at most 95 characters
    wrapped_intro = textwrap.wrap(intro, 95)
    # draw the overview paragraph
    ax.text(0.09, 0.855, "\n".join(wrapped_intro), fontsize=10.5, color=PDF_BODY,
            family='serif', va='top', linespacing=1.7)

    # work out where the facts table starts (below the paragraph)
    y = 0.855 - 0.026 * len(wrapped_intro) - 0.045
    # the facts to show: (label, value)
    stats = [
        ("Dataset", dataset_name),
        ("Rows", f"{row_count:,}"),
        ("Columns", f"{column_count} ({numeric_count} numeric, {categorical_count} categorical)"),
        ("Charts in this report", str(len(queue))),
    ]
    for label, val in stats:
        # the label in bold on the left
        ax.text(0.09, y, label, fontsize=10, fontweight='bold', color=PDF_HEADING, family='serif', va='top')
        # the value in the middle
        ax.text(0.42, y, val, fontsize=10, color=PDF_BODY, family='serif', va='top')
        # a thin grey line under the row
        ax.add_patch(Rectangle((0.09, y - 0.023), 0.82, 0.0011, color='#e5e7eb', transform=ax.transAxes))
        # move down for the next row
        y -= 0.033

    # leave a little space, then list the charts included
    y -= 0.02
    ax.text(0.09, y, "The analysis section contains:", fontsize=10.5, color=PDF_HEADING, family='serif', va='top')
    y -= 0.03
    for item in queue:
        # a bullet (\u2022) followed by the chart's title
        ax.text(0.11, y, f"\u2022 {item['title']}", fontsize=10, color=PDF_BODY, family='serif', va='top')
        y -= 0.026

    # save the page and close it
    pdf_pages.savefig(fig)
    plt.close(fig)


# ------------------------------------------------------------
# CHART + INSIGHTS PAGE (one page per queued item)
# ------------------------------------------------------------

# Helper: converts a matplotlib chart into an image array that can be placed on a PDF page.
def _chart_fig_to_array(chart_fig):
    # an in-memory file
    buf = io.BytesIO()
    # save the chart as a high-quality (170 dpi) PNG into it
    chart_fig.savefig(buf, format='png', dpi=170, bbox_inches='tight')
    # go back to the start
    buf.seek(0)
    # read the PNG back as an image array
    return plt.imread(buf)


# Draws one chart page: the chart picture plus its three insight sections.
def _render_chart_pdf_page(pdf_pages, chart_fig, item_number, item_title, item_subtitle,
                           key_findings_text, risks_text, recommendations_text,
                           report_title, prepared_by, page_number, show_section_heading):
    # turn the chart into a picture, then close the original figure
    img = _chart_fig_to_array(chart_fig)
    plt.close(chart_fig)

    # a blank page with header and footer
    fig = plt.figure(figsize=(8.5, 11))
    _draw_chrome(fig, report_title, prepared_by, page_number)

    # a full-page drawing area
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis('off')
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # 'y' tracks how far down the page we are drawing (starts near the top)
    y = 0.90
    # the first chart page also gets the big "Chart Analysis" heading
    if show_section_heading:
        ax.text(0.09, y, "Chart Analysis", fontsize=19, fontweight='bold', color=PDF_ACCENT, family='serif', va='top')
        y -= 0.05

    # the chart's number and title
    ax.text(0.09, y, f"{item_number}. {item_title}", fontsize=13.5, fontweight='bold',
            color=PDF_ACCENT, family='serif', va='top')
    y -= 0.026
    # the subtitle, if there is one
    if item_subtitle:
        ax.text(0.09, y, item_subtitle, fontsize=9.5, color=PDF_MUTED, family='serif', va='top')
    y -= 0.03

    # the picture takes 30% of the page height
    img_h = 0.30
    # create a drawing area for the picture
    img_ax = fig.add_axes([0.13, y - img_h, 0.74, img_h])
    img_ax.axis('off')
    # place the chart picture in it
    img_ax.imshow(img)
    # move down below the picture
    y -= img_h + 0.03

    # the three text sections under the chart
    sections = [
        ("Key Findings", key_findings_text),
        ("Risks & Opportunities", risks_text),
        ("Strategic Recommendations", recommendations_text),
    ]
    for heading, text in sections:
        # the section heading
        ax.text(0.09, y, heading, fontsize=11, fontweight='bold', color=PDF_HEADING, family='serif', va='top')
        y -= 0.026

        # turn each non-empty line of text into a wrapped bullet point
        lines = []
        for line in (text or '').splitlines():
            if line.strip():
                lines.extend(textwrap.wrap(f"\u2022 {line.strip()}", 100))
        # join the lines, or show a dash if there is no text
        block = "\n".join(lines) if lines else "\u2014"

        # draw the text block
        ax.text(0.09, y, block, fontsize=9.7, color=PDF_BODY, family='serif', va='top', linespacing=1.65)
        # move down by the height of the text, plus some spacing
        y -= 0.021 * max(len(lines), 1) + 0.028

    # save the page and close it
    pdf_pages.savefig(fig)
    plt.close(fig)


# ------------------------------------------------------------
# REPORT BUILDER PDF EXPORT (cover + TOC + overview + one
# page per queued chart)
# ------------------------------------------------------------

# The view that creates and downloads the full PDF report ("Download PDF Report" button).
@login_required
def export_report_pdf(request):
    # the user's newest dataset and their queued charts
    dataset = Dataset.objects.filter(user=request.user).order_by('-uploaded_at').first()
    queue = request.session.get(REPORT_SESSION_KEY, [])

    # we need a dataset and at least one chart
    if not dataset or not queue:
        return HttpResponse("No charts have been added to the report yet.", status=400)

    # load the dataset as a table
    df = get_dataset_dataframe(dataset)
    # an empty table cannot be exported
    if df.empty:
        return HttpResponse("No dataset available to export.", status=404)

    # add financial ratios (if it is a financial dataset) and detect column types
    df, ratio_columns, is_financial, _mapping = add_financial_ratios(df)
    numeric_columns, categorical_columns = detect_column_types(df)

    # read the report details typed on the Report Builder page
    report_title = request.POST.get('report_title', 'QuickSnap')
    subtitle = request.POST.get('subtitle', '')
    # "Prepared By" is always the logged-in account — never taken from the
    # request, so it can't be spoofed by editing the form in devtools.
    prepared_by = request.user.get_full_name() or request.user.get_username()
    report_date = request.POST.get('report_date', '')
    organization = request.POST.get('organization', '')
    executive_overview = request.POST.get('executive_overview', '')

    # an in-memory file to hold the PDF
    buf = io.BytesIO()
    # PdfPages collects pages and writes them into the PDF
    with PdfPages(buf) as pdf_pages:

        # page 1: the cover
        _render_cover_page(pdf_pages, report_title, subtitle, prepared_by, report_date, organization, dataset.name)
        # page 2: the table of contents
        _render_toc_page(pdf_pages, report_title, prepared_by, overview_page_number=3,
                         queue=queue, first_chart_page=4)
        # page 3: the overview
        _render_overview_page(
            pdf_pages, report_title, prepared_by, page_number=3, dataset_name=dataset.name,
            row_count=len(df), column_count=len(df.columns),
            numeric_count=len(numeric_columns), categorical_count=len(categorical_columns),
            queue=queue, executive_overview=executive_overview,
        )

        # the chart pages start at page 4
        page_number = 4
        # one page for each queued chart
        for i, item in enumerate(queue, start=1):
            # the saved chart choices
            config = item['config']
            # try to rebuild the chart
            try:
                chart_fig = build_chart(
                    df, config['chart_type'], config['x_column'], config['y_column'],
                    config['color_column'], config['matrix_columns'], numeric_columns,
                    # charts saved before this option existed have no calculation, so they use 'sum'
                    _clean_agg(config.get('agg')), _clean_date_interval(config.get('date_interval')),
                )
            # if it fails, skip this chart instead of crashing the whole PDF
            except Exception:
                continue

            # draw the chart page (the heading is only shown on the first chart)
            _render_chart_pdf_page(
                pdf_pages, chart_fig, i, item['title'], item['subtitle'],
                item.get('key_findings_text', ''), item.get('risks_text', ''), item.get('recommendations_text', ''),
                report_title, prepared_by, page_number, show_section_heading=(i == 1),
            )
            # next chart goes on the next page
            page_number += 1

    # go back to the start of the in-memory file and read all the PDF bytes
    buf.seek(0)
    pdf_bytes = buf.read()
    # make a safe file name: keep only letters, numbers, spaces, underscores, and dashes
    safe_name = "".join(c for c in report_title if c.isalnum() or c in (' ', '_', '-')).rstrip() or "report"

    # Save the finished PDF + its metadata so it shows up in Report History,
    # and can be re-downloaded later without regenerating anything.
    GeneratedReport.objects.create(
        user=request.user,
        dataset=dataset,
        title=report_title,
        subtitle=subtitle,
        prepared_by=prepared_by,
        organization=organization,
        chart_count=len(queue),
        file=ContentFile(pdf_bytes, name=f"{safe_name}.pdf"),
    )

    # send the PDF to the browser as a download
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{safe_name}.pdf"'
    return response


# ------------------------------------------------------------
# REPORT HISTORY
# ------------------------------------------------------------

# The view for the Report History page.
@login_required
def report_history(request):
    # get all reports made by this user
    reports = GeneratedReport.objects.filter(user=request.user)
    # show them on the page
    return render(request, 'dashboard/report_history.html', {'reports': reports})


# The view that deletes a saved report.
@login_required
def delete_report(request, report_id):
    # find the report (404 error if it doesn't exist or belongs to someone else)
    report = get_object_or_404(GeneratedReport, id=report_id, user=request.user)
    # only delete when the form was submitted
    if request.method == 'POST':
        # delete the PDF file from storage
        report.file.delete(save=False)  # remove the actual PDF file from storage too
        # delete the database record
        report.delete()
    # go back to the history page
    return redirect('report_history')


# ------------------------------------------------------------
# PDF EXPORT — single chart, current Analysis-page selection
# ------------------------------------------------------------

# The view that exports ONE chart as a PDF (no longer linked from the Analysis page,
# since the "Download This Chart" button was removed, but kept here in case you want it later).
@login_required
def export_pdf(request):
    # run the shared setup
    ctx = _prepare_dataset_context(request)
    # no dataset or an empty one
    if ctx is None or ctx['df'].empty:
        return HttpResponse("No dataset available to export.", status=404)

    # take out the dataset and table
    dataset, df = ctx['dataset'], ctx['df']

    # try to build the chart
    try:
        chart_fig = build_chart(
            df, ctx['chart_type'], ctx['x_column'], ctx['y_column'],
            ctx['color_column'], ctx['matrix_columns'], ctx['numeric_columns'], ctx['agg'], ctx['date_interval'],
        )
    # if it fails, tell the user why
    except Exception as e:
        return HttpResponse(f"Could not generate chart: {e}", status=400)

    # write the automatic insights
    insights = generate_insights(
        df, ctx['chart_type'], ctx['x_column'], ctx['y_column'], ctx['color_column'], ctx['agg']
    )
    # copy the key findings list
    key_findings = list(insights['key_findings'])
    # for financial datasets, add the ratio highlights too
    if ctx['is_financial']:
        notes = financial_ratio_narrative(df, ctx['ratio_columns'])
        if notes:
            key_findings = key_findings + ["Financial Ratio Highlights:"] + notes

    # the chart's title and subtitle
    chart_title, chart_subtitle = describe_chart(
        ctx['chart_type'], ctx['x_column'], ctx['y_column'], ctx['color_column'], ctx['agg']
    )

    # the name that appears in the footer
    prepared_by = request.user.get_full_name() or request.user.get_username()

    # an in-memory file to hold the PDF
    buf = io.BytesIO()
    with PdfPages(buf) as pdf_pages:
        # draw the single chart page
        _render_chart_pdf_page(
            pdf_pages, chart_fig, 1, chart_title, chart_subtitle,
            "\n".join(key_findings),
            "\n".join(insights['risks_opportunities']),
            "\n".join(insights['strategic_recommendations']),
            dataset.name, prepared_by, page_number=1, show_section_heading=True,
        )

    # read the PDF bytes
    buf.seek(0)
    # send them to the browser as a download
    response = HttpResponse(buf.read(), content_type='application/pdf')
    # make a safe file name from the dataset name
    safe_name = "".join(c for c in dataset.name if c.isalnum() or c in (' ', '_', '-')).rstrip() or "report"
    response['Content-Disposition'] = f'attachment; filename="{safe_name}_report.pdf"'
    return response