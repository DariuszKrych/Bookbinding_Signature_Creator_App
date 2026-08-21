# Use a specific, locked version of Micromamba
FROM mambaorg/micromamba:1.5.8

# The micromamba image automatically uses user ID 1000 (mambauser)
WORKDIR /app

# Copy the lock file and ensure the correct user owns it
COPY --chown=$MAMBA_USER:$MAMBA_USER conda-lock.yml .

# Install the exact environment and clean up cache to save space
RUN micromamba install --name base --yes --file conda-lock.yml && \
    micromamba clean --all --yes

# Copy the rest of your app code and give ownership to mambauser
COPY --chown=$MAMBA_USER:$MAMBA_USER . .

EXPOSE 8501

# Run the app using micromamba's environment execution
CMD ["micromamba", "run", "-n", "base", "streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]