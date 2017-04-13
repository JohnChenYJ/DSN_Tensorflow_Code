import os
import numpy as np
import tensorflow as tf
from data_reader import H5DataLoader
from TPS_transformer import TPS_transformer
from SpatialDecoderLayer import TPS_decoder
from img_utils import imsave
import ops

class DilatedPixelCNN(object):

    def __init__(self, sess, conf):
        self.sess = sess
        self.conf = conf
        self.conv_size = (3, 3)
        self.pool_size = (2, 2)
        self.tps_out_size = (40,40)
        self.Column_controlP_number = 4
        self.Row_controlP_number = 4    
        self.inserttps = 3
        self.insertdecoder = 3
        self.input_size_D = (40, 40)
        self.out_size_D = (40, 40)
        self.Column_controlP_number_D = 4
        self.Row_controlP_number_D = 4

        self.data_format = 'NHWC'
        self.axis, self.channel_axis = (1, 2), 3
        self.input_shape = [
            conf.batch, conf.height, conf.width, conf.channel]
        self.output_shape = [conf.batch, conf.height, conf.width]
        if not os.path.exists(conf.modeldir):
            os.makedirs(conf.modeldir)
        if not os.path.exists(conf.logdir):
            os.makedirs(conf.logdir)
        if not os.path.exists(conf.sample_dir):
            os.makedirs(conf.sample_dir)
        self.configure_networks()
        self.train_summary = self.config_summary('train')
        self.valid_summary = self.config_summary('valid')

    def configure_networks(self):
        self.build_network()
        optimizer = tf.train.AdamOptimizer(self.conf.learning_rate)
        self.train_op = optimizer.minimize(self.loss_op, name='train_op')
        tf.set_random_seed(self.conf.random_seed)
        self.sess.run(tf.global_variables_initializer())
        trainable_vars = tf.trainable_variables()
        self.saver = tf.train.Saver(var_list=trainable_vars, max_to_keep=0)
        self.writer = tf.summary.FileWriter(self.conf.logdir, self.sess.graph)

    def build_network(self):
        self.inputs = tf.placeholder(
            tf.float32, self.input_shape, name='inputs')
        self.annotations = tf.placeholder(
            tf.int64, self.output_shape, name='annotations')
        expand_annotations = tf.expand_dims(
            self.annotations, -1, name='annotations/expand_dims')
        one_hot_annotations = tf.squeeze(
            expand_annotations, axis=[self.channel_axis],
            name='annotations/squeeze')
        one_hot_annotations = tf.one_hot(
            one_hot_annotations, depth=self.conf.class_num,
            axis=self.channel_axis, name='annotations/one_hot')
        self.predictions = self.inference(self.inputs)
        losses = tf.losses.softmax_cross_entropy(
            one_hot_annotations, self.predictions, scope='loss/losses')
        self.loss_op = tf.reduce_mean(losses, name='loss/loss_op')
        self.decoded_predictions = tf.argmax(
            self.predictions, self.channel_axis, name='accuracy/decode_pred')
        correct_prediction = tf.equal(
            self.annotations, self.decoded_predictions,
            name='accuracy/correct_pred')
        self.accuracy_op = tf.reduce_mean(
            tf.cast(correct_prediction, tf.float32, name='accuracy/cast'),
            name='accuracy/accuracy_op')
        self.m_iou, self.miou_op = tf.contrib.metrics.streaming_mean_iou(
            self.decoded_predictions, self.annotations, self.conf.class_num,
            name='m_iou')

    def config_summary(self, name):
        summarys = []
        summarys.append(tf.summary.scalar(name+'/loss', self.loss_op))
        summarys.append(tf.summary.scalar(name+'/accuracy', self.accuracy_op))
        #if name == 'valid':
        summarys.append(tf.summary.image(
            name+'/input', self.inputs, max_outputs=100))
        summarys.append(tf.summary.image(
            name +
            '/annotation', tf.cast(tf.expand_dims(
                self.annotations, -1), tf.float32),
            max_outputs=100))
        summarys.append(tf.summary.image(
            name +
            '/prediction', tf.cast(tf.expand_dims(
                self.decoded_predictions, -1), tf.float32),
            max_outputs=100))
        summary = tf.summary.merge(summarys)
        return summary

    def inference(self, inputs):
        outputs = inputs
        down_outputs = []
        T_outputs = []
        conv_list = []
        for layer_index in range(self.conf.network_depth-1):
            is_first = True if not layer_index else False
            name = 'down%s' % layer_index
            if layer_index == self.inserttps:
                outputs = self.construct_down_block(outputs, name, down_outputs, T_outputs ,conv_list,first=is_first,TPS = True)
            else:
                outputs = self.construct_down_block(outputs, name, down_outputs,T_outputs ,conv_list, first=is_first,TPS = False)  
            print("down ",layer_index," shape ", outputs.get_shape())          
        outputs = self.construct_bottom_block(outputs, 'bottom')
        print("bottom shape",outputs.get_shape())
        for layer_index in range(self.conf.network_depth-2, -1, -1):
            is_final = True if layer_index == 0 else False
            name = 'up%s' % layer_index
            down_inputs = down_outputs[layer_index]
            if layer_index == self.insertdecoder:
                print("decoder true")
                T = T_outputs[0]
                ori_feature=conv_list[0]
                Decoder = True
                #print("decoder value",Decoder)
            else:
                Decoder = False
                print
                T = []
            print("decoder value",Decoder)
            print("ori_feature",ori_feature)    
            outputs = self.construct_up_block(outputs, down_inputs, name, T,ori_feature,final=is_final,Decoder=Decoder )
            print("up ",layer_index," shape ",outputs.get_shape())
        return outputs

    def construct_down_block(self, inputs, name, down_outputs, cp_outputs,conv_list,first=False,TPS=False):
        num_outputs = self.conf.start_channel_num if first else 2 * \
            inputs.shape[self.channel_axis].value
        conv1 = ops.conv2d(
            inputs, num_outputs, self.conv_size, name+'/conv1')
        if TPS == True:
            conv_list.append(conv1)
            print("tps")
            conv1,T= TPS_transformer(conv1,conv1,self.tps_out_size,self.Column_controlP_number,self.Row_controlP_number)
            cp_outputs.append(T)
        conv2 = ops.conv2d(
            conv1, num_outputs, self.conv_size, name+'/conv2',)
        down_outputs.append(conv2)
        pool = ops.pool2d(
            conv2, self.pool_size, name+'/pool')
        return pool

    def construct_bottom_block(self, inputs, name):
        num_outputs = inputs.shape[self.channel_axis].value
        conv1 = ops.conv2d(
            inputs, 2*num_outputs, self.conv_size, name+'/conv1')
        conv2 = ops.conv2d(
            conv1, num_outputs, self.conv_size, name+'/conv2')
        return conv2

    def construct_up_block(self, inputs, down_inputs, name,T, ori_feature,final = False,Decoder=False):
        num_outputs = inputs.shape[self.channel_axis].value
        conv1 = self.deconv_func()(
            inputs, num_outputs, self.conv_size, name+'/conv1')
        conv1 = tf.concat(
            [conv1, down_inputs], self.channel_axis, name=name+'/concat')
        print("cov1")
        conv2 = self.conv_func()(
            conv1, num_outputs, self.conv_size, name+'/conv2')
        print("conv2")
        if Decoder == True:
            print("decoder")
            conv2 = TPS_decoder(conv2,ori_feature,T,self.input_size_D,self.out_size_D, self.Column_controlP_number_D,self.Row_controlP_number_D)
        num_outputs = self.conf.class_num if final else num_outputs/2
        print("conv3")
        conv3 = ops.conv2d(
            conv2, num_outputs, self.conv_size, name+'/conv3')
        return conv3

    def deconv_func(self):
        return getattr(ops, self.conf.deconv_name)

    def conv_func(self):
        return getattr(ops, self.conf.conv_name)

    def save_summary(self, summary, step):
        print('---->summarizing', step)
        self.writer.add_summary(summary, step)

    def train(self):
        if self.conf.reload_epoch > 0:
            self.reload(self.conf.reload_epoch)
        train_reader = H5DataLoader(self.conf.data_dir+self.conf.train_data)
        valid_reader = H5DataLoader(self.conf.data_dir+self.conf.valid_data)
        for epoch_num in range(self.conf.max_epoch):
            if epoch_num % self.conf.test_step == 1:
                inputs, annotations = valid_reader.next_batch(self.conf.batch)
                feed_dict = {self.inputs: inputs,
                             self.annotations: annotations}
                loss, summary = self.sess.run(
                    [self.loss_op, self.valid_summary], feed_dict=feed_dict)
                self.save_summary(summary, epoch_num)
                print('----testing loss', loss)
            elif epoch_num % self.conf.summary_step == 1:
                inputs, annotations = train_reader.next_batch(self.conf.batch)
                feed_dict = {self.inputs: inputs,
                             self.annotations: annotations}
                loss, _, summary = self.sess.run(
                    [self.loss_op, self.train_op, self.train_summary],
                    feed_dict=feed_dict)
                self.save_summary(summary, epoch_num)
            else:
                inputs, annotations = train_reader.next_batch(self.conf.batch)
                feed_dict = {self.inputs: inputs,
                             self.annotations: annotations}
                loss, _ = self.sess.run(
                    [self.loss_op, self.train_op], feed_dict=feed_dict)
                print('----training loss', loss)
            if epoch_num % self.conf.save_step == 1:
                self.save(epoch_num)

    def test(self):
        print('---->testing ', self.conf.test_epoch)
        if self.conf.test_epoch > 0:
            self.reload(self.conf.test_epoch)
        else:
            print("please set a reasonable test_epoch")
            return
        valid_reader = H5DataLoader(
            self.conf.data_dir+self.conf.valid_data, False)
        self.sess.run(tf.local_variables_initializer())
        count = 0
        losses = []
        accuracies = []
        m_ious = []
        while True:
            inputs, annotations = valid_reader.next_batch(self.conf.batch)
            if inputs.shape[0] < self.conf.batch:
                break
            feed_dict = {self.inputs: inputs, self.annotations: annotations}
            loss, accuracy, m_iou, _ = self.sess.run(
                [self.loss_op, self.accuracy_op, self.m_iou, self.miou_op],
                feed_dict=feed_dict)
            print('values----->', loss, accuracy, m_iou)
            count += 1
            losses.append(loss)
            accuracies.append(accuracy)
            m_ious.append(m_iou)
        print('Loss: ', np.mean(losses))
        print('Accuracy: ', np.mean(accuracies))
        print('M_iou: ', m_ious[-1])

    def predict(self):
        print('---->predicting ', self.conf.test_epoch)
        if self.conf.test_epoch > 0:
            self.reload(self.conf.test_epoch)
        else:
            print("please set a reasonable test_epoch")
            return
        test_reader = H5DataLoader(
            self.conf.data_dir+self.conf.test_data, False)
        predictions = []
        while True:
            inputs, annotations = test_reader.next_batch(self.conf.batch)
            if inputs.shape[0] < self.conf.batch:
                break
            feed_dict = {self.inputs: inputs, self.annotations: annotations}
            predictions.append(self.sess.run(
                self.decoded_predictions, feed_dict=feed_dict))
        print('----->saving predictions')
        for index, prediction in enumerate(predictions):
            for i in range(prediction.shape[0]):
                imsave(prediction[i], self.conf.sample_dir +
                       str(index*prediction.shape[0]+i)+'.png')

    def save(self, step):
        print('---->saving', step)
        checkpoint_path = os.path.join(
            self.conf.modeldir, self.conf.model_name)
        self.saver.save(self.sess, checkpoint_path, global_step=step)

    def reload(self, step):
        checkpoint_path = os.path.join(
            self.conf.modeldir, self.conf.model_name)
        model_path = checkpoint_path+'-'+str(step)
        if not os.path.exists(model_path+'.meta'):
            print('------- no such checkpoint', model_path)
            return
        self.saver.restore(self.sess, model_path)
