# Frontend dev-mode container (G-4 boot placeholder). Runs the Vite dev
# server so it's live-reloadable during G-6/G-7 development. A production
# build stage is out of scope here -- Vercel deploy is a future task.
FROM node:20-slim

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
