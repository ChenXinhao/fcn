#!/usr/bin/env python

from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from collections import defaultdict
import os
import os.path as osp

from chainer import cuda
import chainer.optimizers as O
import chainer.serializers as S
from chainer import Variable
import numpy as np
import tqdm

import fcn
from fcn.models import FCN32s
from fcn.models import VGG16
from fcn import pascal
from fcn import util


class Trainer(object):

    def __init__(self, weight_decay, test_interval, max_iter, snapshot, gpu):
        self.weight_decay = weight_decay
        self.test_interval = test_interval
        self.max_iter = max_iter
        self.snapshot = snapshot
        self.gpu = gpu
        # setup logging
        self._setup_logging()
        # pretrained model
        pretrained_model = self._setup_pretrained_model()
        # dataset
        self.dataset = pascal.SegmentationClassDataset()
        # setup fcn8s
        self.model = FCN32s(n_class=len(self.dataset.target_names))
        fcn.util.copy_chainermodel(pretrained_model, self.model)
        if self.gpu != -1:
            self.model.to_gpu(self.gpu)
        # setup optimizer
        self.optimizer = O.MomentumSGD(lr=1e-10, momentum=0.99)
        self.optimizer.setup(self.model)

    def __del__(self):
        self.logfile.close()

    def _setup_logging(self):
        self.log_dir = 'snapshot'
        if not osp.exists(self.log_dir):
            os.makedirs(self.log_dir)
            print("Created '{0}'".format(self.log_dir))
        self.logfile = open(osp.join(self.log_dir, 'log.csv'), 'w')
        self.logfile.write('i_iter,type,loss,acc,acc_cls,iu,fwavacc\n')

    def _setup_pretrained_model(self):
        pretrained_model_path = fcn.setup.download_vgg16_chainermodel()
        pretrained_model = VGG16()
        print('Loading pretrained model: {0}'.format(pretrained_model_path))
        S.load_hdf5(pretrained_model_path, pretrained_model)
        return pretrained_model

    def _iterate_once(self, type, indices=None):
        """Iterate with train/val once."""
        batch = self.dataset.next_batch(
            batch_size=1, type=type, indices=indices)
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
            self.optimizer.weight_decay(self.weight_decay)
        else:
            self.model(x, y)
        # evaluate
        label_pred = np.argmax(cuda.to_cpu(self.model.score.data), axis=1)
        acc, acc_cls, iu, fwavacc = util.label_accuracy_score(
            label, label_pred, self.model.n_class)
        loss = float(cuda.to_cpu(self.model.loss.data))
        return loss, acc, acc_cls, iu, fwavacc

    def train(self):
        """Iterate with train data."""
        log_templ = ('{i_iter}: type={type}, loss={loss}, acc={acc}, '
                     'acc_cls={acc_cls}, iu={iu}, fwavacc={fwavacc}')
        for i_iter in xrange(self.max_iter):
            self.i_iter = i_iter

            if i_iter % self.test_interval == 0:
                self.validate()

            type = 'train'
            self.model.train = True
            loss, acc, acc_cls, iu, fwavacc = self._iterate_once(type=type)
            log = dict(i_iter=self.i_iter, type=type, loss=loss, acc=acc,
                       acc_cls=acc_cls, iu=iu, fwavacc=fwavacc)
            print(log_templ.format(**log))
            self.logfile.write(
                '{i_iter},{type},{loss},{acc},{acc_cls},{iu},{fwavacc}\n'
                .format(**log))

            if i_iter % self.snapshot == 0:
                print('{0}: saving snapshot...'.format(i_iter))
                snapshot_model = osp.join(
                    self.log_dir,
                    'fcn8s_{0}.chainermodel'.format(i_iter))
                snapshot_optimizer = osp.join(
                    self.log_dir, 'fcn8s_optimizer_{0}.h5'.format(i_iter))
                S.save_hdf5(snapshot_model, self.model)
                S.save_hdf5(snapshot_optimizer, self.optimizer)

    def validate(self):
        """Validate training with data."""
        log_templ = ('{i_iter}: type={type}, loss={loss}, acc={acc}, '
                     'acc_cls={acc_cls}, iu={iu}, fwavacc={fwavacc}')
        type = 'val'
        self.model.train = False
        N_data = len(self.dataset.val)
        result = defaultdict(list)
        desc = '{0}: validating'.format(self.i_iter)
        for indice in tqdm.tqdm(xrange(N_data), ncols=80, desc=desc):
            loss, acc, acc_cls, iu, fwavacc = self._iterate_once(
                type=type, indices=[indice])
            result['loss'].append(loss)
            result['acc'].append(acc)
            result['acc_cls'].append(acc_cls)
            result['iu'].append(iu)
            result['fwavacc'].append(fwavacc)
        log = dict(
            i_iter=self.i_iter,
            type=type,
            loss=np.array(result['loss']).mean(),
            acc=np.array(result['acc']).mean(),
            acc_cls=np.array(result['acc_cls']).mean(),
            iu=np.array(result['iu']).mean(),
            fwavacc=np.array(result['fwavacc']).mean(),
        )
        print(log_templ.format(**log))
        self.logfile.write(
            '{i_iter},{type},{loss},{acc},{acc_cls},{iu},{fwavacc}\n'
            .format(**log))


if __name__ == '__main__':
    gpu = 0
    trainer = Trainer(
        weight_decay=0.0005,
        test_interval=1000,
        max_iter=100000,
        snapshot=4000,
        gpu=gpu,
    )
    trainer.train()
