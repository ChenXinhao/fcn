#!/usr/bin/env python

from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals
import os.path as osp

from chainer import cuda
import chainer.optimizers as O
import chainer.serializers as S
from chainer import Variable
import numpy as np
import progressbar

import fcn
from fcn.models import FCN8s
from fcn.models import VGG16
from fcn import pascal


class Trainer(object):

    def __init__(self, gpu):
        self.gpu = gpu
        # pretrained model
        pretrained_model = self._setup_pretrained_model()
        # dataset
        self.dataset = pascal.SegmentationClassDataset()
        # setup fcn8s
        self.model = FCN8s(n_class=len(self.dataset.target_names))
        print('Copying pretrained_model...')
        fcn.util.copy_chainermodel(pretrained_model, self.model)
        if self.gpu != -1:
            self.model.to_gpu(self.gpu)
        # setup optimizer
        self.optimizer = O.Adam()
        self.optimizer.setup(self.model)

    def _setup_pretrained_model(self):
        data_dir = fcn.get_data_dir()
        pretrained_model_path = osp.join(data_dir, 'vgg16.chainermodel')
        md5 = '292e6472062392f5de02ef431bba4a48'
        if not (osp.exists(pretrained_model_path) and
                fcn.util.check_md5(pretrained_model_path, md5)):
            url = 'https://www.dropbox.com/s/oubwxgmqzep24yq/VGG.model?dl=0'
            fcn.util.download_data('fcn', pretrained_model_path, url, md5)
        pretrained_model = VGG16()
        print('Loading pretrained model: {0}'.format(pretrained_model_path))
        S.load_hdf5(pretrained_model_path, pretrained_model)
        return pretrained_model

    def batch_loop(self, type):
        """Batch loop.

        Args:

            - type (str): train, trainval, or val

        .. note::

            FCN8s does only supports one element batch.
        """
        self.model.train = True if type == 'train' else False
        N_data = len(self.dataset[type])
        sum_loss, sum_accuracy = 0, 0
        pbar = progressbar.ProgressBar(max_value=N_data)
        for i in xrange(0, N_data):
            pbar.update(i+1)
            # load batch
            batch = self.dataset.next_batch(batch_size=1, type=type)
            img, label = batch.img[0], batch.label[0]
            # x
            x_datum = self.dataset.img_to_datum(img)
            x_data = np.array([x_datum], dtype=np.float32)
            if self.gpu != -1:
                x_data = cuda.to_gpu(x_data, device=self.gpu)
            x = Variable(x_data, volatile=not self.model.train)
            # y
            y_data = np.array([label], dtype=np.int32)
            if self.gpu != -1:
                y_data = cuda.to_gpu(y_data, device=self.gpu)
            y = Variable(y_data, volatile=not self.model.train)
            # optimize
            if self.model.train:
                self.optimizer.zero_grads()
                self.optimizer.update(self.model, x, y)
            else:
                self.model(x, y)
            sum_loss += cuda.to_cpu(self.model.loss.data) * len(batch)
            sum_accuracy += self.model.accuracy * len(batch)
        pbar.finish()
        mean_loss = sum_loss / N_data
        mean_accuracy = sum_accuracy / N_data
        return mean_loss, mean_accuracy

    def main_loop(self):
        log_csv = osp.join(fcn.get_data_dir(), 'log.csv')
        for epoch in xrange(100):
            for type in ['train', 'trainval', 'val']:
                mean_loss, mean_accuracy = self.batch_loop(type=type)
                log = dict(epoch=epoch, type=type, loss=mean_loss,
                           accuracy=mean_accuracy)
                print('epoch{epoch}: type: {type}, mean_loss: {loss}, '
                      'mean_accuracy: {accuracy}'.format(**log))
                with open(log_csv, 'a') as f:
                    f.write('{epoch},{type},{loss},{accuracy}\n'.format(**log))


if __name__ == '__main__':
    gpu = 0
    trainer = Trainer(gpu=gpu)
    trainer.main_loop()
