"""production — filming/editing jobs with optional product and media associations (M2B).

Depends on identity, media, products and procurement. Media assets never carry a job id:
associations live in `production_job_media` and are optional in both directions.
"""
