# Dataset memo

Draft descriptions for `datasets.yaml`. Column meanings are inferred; good enough for sample data.

---

## movies

`movies/movies.csv` → `data/db/movies.db`, table `movies` (5,000 rows, synthetic)

Movie budgets, box-office revenue and ratings, 1980–2024. Answers questions about genres, profit, ROI and which movies became blockbusters.

| Column | Type | Description |
|---|---|---|
| movie_id | INTEGER | Unique movie ID |
| title | TEXT | Placeholder title, e.g. `Movie_0001` |
| year | INTEGER | Release year (1980–2024) |
| decade | TEXT | Release decade: `1980s` … `2020s` |
| genre | TEXT | Action, Adventure, Animation, Comedy, Documentary, Drama, Horror, Romance, Sci-Fi, Thriller |
| runtime_min | INTEGER | Length in minutes (60–185) |
| budget_usd | REAL | Production budget in USD |
| revenue_usd | REAL | Box-office revenue in USD |
| profit_usd | REAL | revenue_usd − budget_usd |
| roi | REAL | Return on investment: profit_usd / budget_usd (0.31 = 31%) |
| rating | REAL | Average audience rating (1–10) |
| vote_count | INTEGER | Number of ratings |
| popularity | REAL | Popularity score (higher = more popular) |
| blockbuster | INTEGER | 1 if revenue > 3× budget AND revenue > $100M, else 0 |

---

## videos

`videos/videos.csv` → `data/db/videos.db`, table `videos` (12,000 rows, synthetic)

YouTube-style videos with upload details and engagement. Answers questions about views, likes, best upload times, categories and what makes a video go viral.

| Column | Type | Description |
|---|---|---|
| video_id | TEXT | Unique video ID, e.g. `vid_000001` |
| category | TEXT | Beauty, Comedy, Cooking, DIY, Education, Finance, Fitness, Gaming, Music, News, Science, Sports, Tech, Travel, Vlog |
| duration_sec | INTEGER | Video length in seconds (30–1960) |
| title_length | INTEGER | Number of characters in the title |
| thumbnail_face | INTEGER | 1 if the thumbnail shows a face, else 0 |
| upload_hour | INTEGER | Hour of upload (0–23) |
| upload_day | TEXT | Day of upload: Mon, Tue, Wed, Thu, Fri, Sat, Sun |
| tags_count | INTEGER | Number of tags |
| description_length | INTEGER | Number of characters in the description |
| subscriber_count | INTEGER | Channel's subscribers at upload time |
| views | INTEGER | Total views |
| likes | INTEGER | Total likes |
| comments | INTEGER | Total comments |
| shares | INTEGER | Total shares |
| viral | INTEGER | 1 if views are in the top 20%, else 0 |

---

## food_delivery

`food_delivery/customers.csv` → `data/db/food_delivery.db`, table `customers` (388 rows)

Survey of online food delivery customers in Bangalore, India. Answers questions about who orders (age, job, income, family) and how customer groups rate the service.

| Column | Type | Description |
|---|---|---|
| customer_id | INTEGER | Unique survey response ID (added; source had none) |
| age | INTEGER | Customer age (18–33) |
| gender | TEXT | Male, Female |
| marital_status | TEXT | Single, Married, Prefer not to say |
| occupation | TEXT | Student, Employee, Self Employed, House wife |
| monthly_income_rs | INTEGER | Estimated monthly income in rupees (midpoint of the survey band): 0 = No Income, 5000 = Below 10000, 17500 = 10001–25000, 37500 = 25001–50000, 60000 = More than 50000 (assumed) |
| education | TEXT | Uneducated, School, Graduate, Post Graduate, Ph.D |
| family_size | INTEGER | People in the household (1–6) |
| customer_type | TEXT | New, Regular, Frequent |
| pin_code | INTEGER | Bangalore postal code of the customer's area |
| feedback | TEXT | Overall feedback on the service: Positive, Negative |

Cleaning applied: snake_case column names; dropped an unnamed duplicate column, `Output` (unclear) and `latitude`/`longitude`; fixed `Negative ` (trailing space) and `Self Employeed`; replaced the text `Monthly Income` bands with numeric `monthly_income_rs` (band midpoint); added `customer_id` (103 rows are identical survey answers, so rows need a key).
