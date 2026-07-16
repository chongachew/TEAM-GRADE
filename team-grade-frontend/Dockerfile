FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# CRA bakes REACT_APP_* vars into the static bundle at build time, not
# runtime - must be passed as a build arg, matching apps/web's
# NEXT_PUBLIC_* build-arg pattern for the same reason.
ARG REACT_APP_API_BASE
ENV REACT_APP_API_BASE=$REACT_APP_API_BASE
RUN npm run build

FROM nginx:1.27-alpine AS runner
COPY --from=build /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 3500
