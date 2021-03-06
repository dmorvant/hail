include ../config.mk

EXTRA_PYTHONPATH := ../hail/python:../gear
PYTHON := PYTHONPATH=$${PYTHONPATH:+$${PYTHONPATH}:}$(EXTRA_PYTHONPATH) python3

QUERY_LATEST = gcr.io/$(PROJECT)/query:latest
QUERY_IMAGE = gcr.io/$(PROJECT)/query:$(shell docker images -q --no-trunc query | sed -e 's,[^:]*:,,')

.PHONY: check
check:
	$(PYTHON) -m flake8  --config ../setup.cfg query
	$(PYTHON) -m pylint --rcfile ../pylintrc query --score=n

.PHONY: build
build:
	$(MAKE) -C ../docker build
	$(MAKE) -C ../hail shadowJar
# janky
	cp ../hail/build/libs/hail-all-spark.jar ./hail.jar
	-docker pull $(QUERY_LATEST)
	python3 ../ci/jinja2_render.py '{"service_base_image":{"image":"service-base"}}' Dockerfile Dockerfile.out
	docker build -t query -f Dockerfile.out --cache-from query,$(QUERY_LATEST),service-base ..

.PHONY: push
push: build
	docker tag query $(QUERY_LATEST)
	docker push $(QUERY_LATEST)
	docker tag query $(QUERY_IMAGE)
	docker push $(QUERY_IMAGE)

.PHONY: deploy
deploy: push
	kubectl -n default apply -f service-account.yaml
	python3 ../ci/jinja2_render.py '{"default_ns":{"name":"default"}}' service-account-batch-pods.yaml service-account-batch-pods.yaml.out
	kubectl -n batch-pods apply -f service-account-batch-pods.yaml.out
	python3 ../ci/jinja2_render.py '{"code":{"sha":"$(shell git rev-parse --short=12 HEAD)"},"deploy":true,"query_image":{"image":"$(QUERY_IMAGE)"},"batch_pods_ns":{"name":"batch-pods"},"global":{"project":"$(PROJECT)","domain":"$(DOMAIN)"}}' deployment.yaml deployment.yaml.out
	kubectl -n default apply -f deployment.yaml.out