# MPD Analytics Suite Backend (FastAPI)

This repository contains the FastAPI backend for the Managed Pressure Drilling (MPD) Analytics Suite. It is designed to perform engineering calculations, process data, and expose API endpoints for the Next.js frontend.

## Project Structure

```
mpd_backend/
├── main.py
├── utils.py
├── requirements.txt
└── Dockerfile
```

*   `main.py`: The main FastAPI application file, defining API routes and Pydantic models.
*   `utils.py`: Contains the core engineering calculation logic using NumPy and SciPy.
*   `requirements.txt`: Lists all Python dependencies required for the project.
*   `Dockerfile`: Defines the Docker image for containerizing the application, essential for deployment on platforms like Railway.

## Engineering Calculations

The `utils.py` file includes placeholder functions for key hydraulic calculations:

*   `calculate_friction_pressure_loss`: A simplified model for calculating friction pressure loss in the annulus.
*   `calculate_ecd_equation`: An equation used by `scipy.optimize.fsolve` to determine the Equivalent Circulating Density (ECD).
*   `calculate_hydraulics`: The main function that orchestrates the calculation of friction pressure loss, ECD, Bottom Hole Pressure (BHP), and choke pressure requirement based on well parameters.

**Note**: The current calculations are simplified for demonstration purposes. For a production-grade engineering platform, these models would need to be replaced with rigorous, validated hydraulic and MPD algorithms.

## API Endpoints

The FastAPI application exposes the following endpoints:

*   `POST /calculate_hydraulics`: Accepts `WellParameters` (mud_density, flow_rate, drill_pipe_od, drill_pipe_id, annulus_od, annulus_id, temperature, depth) and returns `HydraulicCalculationResult` (friction_pressure_loss, ecd, bhp, choke_pressure_requirement).
*   `GET /health`: A simple health check endpoint that returns `{"status": "ok"}`.

## Local Development

1.  **Navigate to the project directory:**
    ```bash
    cd mpd_backend
    ```
2.  **Create a virtual environment and install dependencies:**
    ```bash
    python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```
3.  **Run the FastAPI application:**
    ```bash
    uvicorn main:app --reload
    ```
    The API will be accessible at `http://127.0.0.1:8000` (or `http://localhost:8000`). You can access the interactive API documentation at `http://127.0.0.1:8000/docs`.

## Deployment to Railway

Railway is a platform that allows you to deploy applications directly from your GitHub repository. Here's a step-by-step guide to deploy this FastAPI backend:

1.  **Initialize a Git Repository:**
    If you haven't already, initialize a Git repository in your `mpd_backend` directory and commit your files.
    ```bash
    git init
    git add .
    git commit -m "Initial FastAPI backend setup"
    ```
2.  **Create a GitHub Repository:**
    Create a new private or public repository on GitHub (e.g., `mpd-analytics-backend`) and push your local repository to it.
    ```bash
    git remote add origin https://github.com/your-username/mpd-analytics-backend.git
    git branch -M main
    git push -u origin main
    ```
    Replace `your-username` with your actual GitHub username.

3.  **Create a New Project on Railway:**
    *   Go to [Railway.app](https://railway.app/) and log in.
    *   Click on `New Project`.
    *   Select `Deploy from GitHub repo`.

4.  **Connect GitHub and Select Repository:**
    *   Authorize Railway to access your GitHub account if prompted.
    *   Select the `mpd-analytics-backend` repository you just created.

5.  **Configure Deployment Settings:**
    Railway will automatically detect the `Dockerfile` in your repository. It will use this to build and deploy your application.
    *   **Build Command**: Railway should automatically detect `docker build . -t railway/image` or similar. If not, you might need to specify it.
    *   **Start Command**: Railway should automatically detect `uvicorn main:app --host 0.0.0.0 --port $PORT`. Ensure that the port is set to `$PORT` as Railway injects the port dynamically via an environment variable.
    *   **Environment Variables**: For this basic setup, no specific environment variables are strictly required. However, for production, you would add variables like `DATABASE_URL` for Supabase connection, API keys, etc., here.

6.  **Deploy:**
    *   Click `Deploy`.
    *   Railway will now build your Docker image and deploy your application. You can monitor the build and deployment logs in the Railway dashboard.

7.  **Access Your Deployed API:**
    Once deployed, Railway will provide a public URL for your service. You can find this URL in your project dashboard under the service settings. It will look something like `https://mpd-analytics-backend-production.up.railway.app/`.

## Frontend Integration (Next.js)

Your Next.js frontend will interact with this deployed FastAPI backend by making HTTP requests to the public URL provided by Railway. For example:

```javascript
// Example in your Next.js frontend
const response = await fetch('https://mpd-analytics-backend-production.up.railway.app/calculate_hydraulics', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
    },
    body: JSON.stringify({
        mud_density: 11.2,
        flow_rate: 600,
        drill_pipe_od: 5.0,
        drill_pipe_id: 4.276,
        annulus_od: 8.5,
        annulus_id: 5.0,
        temperature: 150,
        depth: 12500
    }),
});
const data = await response.json();
console.log(data);
```

Remember to handle CORS (Cross-Origin Resource Sharing) in your FastAPI application if your Next.js frontend is hosted on a different domain. You can use `fastapi.middleware.cors.CORSMiddleware` for this.

```python
# In main.py, after app = FastAPI()
from fastapi.middleware.cors import CORSMiddleware

origins = [
    "http://localhost:3000",  # Your Next.js development server
    "https://www.msproject-pied.vercel.app", # Your Vercel deployment
    # Add other frontend origins as needed
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## Next Steps

*   **Refine Engineering Models**: Replace placeholder calculations in `utils.py` with accurate and validated MPD hydraulic models.
*   **Implement Data Processing**: Add logic for handling and transforming drilling data for various visualizations.
*   **Expand API Endpoints**: Develop additional API endpoints for other functionalities like well design data storage, real-time monitoring data ingestion, and report generation.
*   **Database Integration**: Integrate with Supabase PostgreSQL for persistent storage of well data, formation data, and calculation results.
