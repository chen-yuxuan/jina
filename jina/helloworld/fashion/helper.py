import gzip
import os
import random
import urllib.request
import webbrowser
from collections import defaultdict

import numpy as np

from jina import Document
from jina.helper import colored
from jina.logging.predefined import default_logger
from jina.logging.profile import ProgressBar

result_html = []
top_k = 0
num_docs_evaluated = 0
evaluation_value = defaultdict(float)


def index_generator(num_docs: int, target: dict):
    """
    Generate the index data.

    :param num_docs: Number of documents to be indexed.
    :param target: Dictionary which stores the data paths
    :yields: index data
    """
    for internal_doc_id in range(num_docs):
        # x_blackwhite.shape is (28,28)
        x_blackwhite = 255 - target['index']['data'][internal_doc_id]
        # x_color.shape is (28,28,3)
        x_color = np.stack((x_blackwhite,) * 3, axis=-1)
        d = Document(content=x_color)
        d.tags['id'] = internal_doc_id
        yield d


def query_generator(num_docs: int, target: dict):
    """
    Generate the query data.

    :param num_docs: Number of documents to be queried
    :param target: Dictionary which stores the data paths
    :yields: query data
    """
    for _ in range(num_docs):
        num_data = len(target['query-labels']['data'])
        idx = random.randint(0, num_data - 1)
        # x_blackwhite.shape is (28,28)
        x_blackwhite = 255 - target['query']['data'][idx]
        # x_color.shape is (28,28,3)
        x_color = np.stack((x_blackwhite,) * 3, axis=-1)
        d = Document(content=x_color)

        yield d


def print_result(resp):
    """
    Callback function to receive results.

    :param resp: returned response with data
    """
    global top_k
    global evaluation_value
    for d in resp.docs:
        vi = d.uri
        result_html.append(f'<tr><td><img src="{vi}"/></td><td>')
        top_k = len(d.matches)
        for kk in d.matches:
            kmi = kk.uri
            result_html.append(
                f'<img src="{kmi}" style="opacity:{kk.scores["cosine"].value}"/>'
            )
        result_html.append('</td></tr>\n')

        # update evaluation values
        # as evaluator set to return running avg, here we can simply replace the value
        for k, evaluation in d.evaluations.items():
            evaluation_value[k] = evaluation.value


def write_html(html_path):
    """
    Method to present results in browser.

    :param html_path: path of the written html
    """

    with open(
        os.path.join(os.path.dirname(os.path.realpath(__file__)), 'demo.html')
    ) as fp, open(html_path, 'w') as fw:
        t = fp.read()
        t = t.replace('{% RESULT %}', '\n'.join(result_html))
        t = t.replace(
            '{% PRECISION_EVALUATION %}',
            '{:.2f}%'.format(evaluation_value['Precision'] * 100.0),
        )
        t = t.replace(
            '{% RECALL_EVALUATION %}',
            '{:.2f}%'.format(evaluation_value['Recall'] * 100.0),
        )
        t = t.replace('{% TOP_K %}', str(top_k))
        fw.write(t)

    url_html_path = 'file://' + os.path.abspath(html_path)

    try:
        webbrowser.open(url_html_path, new=2)
    except:
        pass  # intentional pass, browser support isn't cross-platform
    finally:
        default_logger.info(
            f'You should see a "demo.html" opened in your browser, '
            f'if not you may open {url_html_path} manually'
        )

    colored_url = colored(
        'https://github.com/jina-ai/jina', color='cyan', attrs='underline'
    )
    default_logger.info(
        f'🤩 Intrigued? Play with `jina hello fashion --help` and learn more about Jina at {colored_url}'
    )


def download_data(targets, download_proxy=None, task_name='download fashion-mnist'):
    """
    Download data.

    :param targets: target path for data.
    :param download_proxy: download proxy (e.g. 'http', 'https')
    :param task_name: name of the task
    """
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-agent', 'Mozilla/5.0')]
    if download_proxy:
        proxy = urllib.request.ProxyHandler(
            {'http': download_proxy, 'https': download_proxy}
        )
        opener.add_handler(proxy)
    urllib.request.install_opener(opener)
    with ProgressBar(description=task_name) as t:
        for k, v in targets.items():
            if not os.path.exists(v['filename']):
                urllib.request.urlretrieve(
                    v['url'], v['filename'], reporthook=lambda *x: t.update(0.01)
                )
            if k == 'index-labels' or k == 'query-labels':
                v['data'] = load_labels(v['filename'])
            if k == 'index' or k == 'query':
                v['data'] = load_mnist(v['filename'])


def load_mnist(path):
    """
    Load MNIST data

    :param path: path of data
    :return: MNIST data in np.array
    """

    with gzip.open(path, 'rb') as fp:
        return np.frombuffer(fp.read(), dtype=np.uint8, offset=16).reshape([-1, 28, 28])


def load_labels(path: str):
    """
    Load labels from path

    :param path: path of labels
    :return: labels in np.array
    """
    with gzip.open(path, 'rb') as fp:
        return np.frombuffer(fp.read(), dtype=np.uint8, offset=8).reshape([-1, 1])
